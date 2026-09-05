#!/usr/bin/env python3
"""gen_comfy.py v3 — API nativa de ComfyUI (18188), txt2img + img2img.
Uso: PROMPT por stdin. Env:
  MODEL=qwen|realvisxl, LORA=none|qwen4play|pov|nudify, INPUT_IMAGE=<archivo en input/>
Imprime el path local del output.
"""
import json, os, sys, time, urllib.request

API = "http://127.0.0.1:18188"
BASE = "/workspace/ComfyUI"

def post(path, payload):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())

def get(path):
    with urllib.request.urlopen(API + path, timeout=60) as r:
        return json.loads(r.read())

def main():
    prompt_text = sys.stdin.read().strip()
    model = os.environ.get("MODEL", "qwen")
    lora = os.environ.get("LORA", "none")
    in_img = os.environ.get("INPUT_IMAGE", "")

    if model == "qwen":
        clip_src, model_src = ["2", 0], ["1", 0]
        lora_default = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors"
        lora_ok = lora == "lightning"
    elif model == "krea2":
        clip_src, model_src = ["2", 0], ["1", 0]
        lora_default = "Krea2NSFWV4.safetensors"
        lora_ok = lora in ("krea_nsfw", "krea_snapshot", "krea_both")
        if lora == "krea_snapshot":
            lora_default = "RealisticSnapshotKrea2.safetensors"
    elif model == "krea2_moody":
        # Moody V6.0 como base: autor usa Euler A + beta, 8 pasos (12 para detalle)
        clip_src, model_src = ["2", 0], ["1", 0]
        lora_default = "Krea2NSFWV4.safetensors"
        lora_ok = lora in ("krea_nsfw", "krea_snapshot", "krea_both")
        if lora == "krea_snapshot":
            lora_default = "RealisticSnapshotKrea2.safetensors"
    elif model == "wan_i2v":
        clip_src, model_src = ["2", 0], ["1", 0]
        lora_default = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
        lora_ok = lora == "lightx2v"
        if not in_img:
            sys.stderr.write("wan_i2v requiere INPUT_IMAGE (imagen->video)\n")
            sys.exit(2)
    elif model == "minimax_i2v":
        # MiniMax H3 (i2v con audio nativo): sin LoRA por ahora (opciones futuras)
        clip_src, model_src = ["2", 0], ["1", 0]
        lora_ok = False
        if not in_img:
            sys.stderr.write("minimax_i2v requiere INPUT_IMAGE (imagen->video)\n")
            sys.exit(2)
    else:
        clip_src, model_src = ["1", 1], ["1", 0]
        lora_default = "NsfwPovAllInOneSdxlMINI.safetensors"
        lora_ok = lora in ("pov", "nudify")
        if lora == "nudify":
            lora_default = "nudify_xl_lite.safetensors"

    if model == "qwen" and lora in ("multiangle", "multiangle_batch"):
        # Flujo multi-ángulo con ángulos ALEATORIOS (nodo RandomCameraAngles):
        # el prompt del usuario (stdin) = la "segunda parte" (instrucción de edición)
        # → se inyecta en el CR Prompt Text de instrucción (#295 simple / #313 batch).
        # La cámara sigue generando el ángulo aleatorio en cada pasada.
        sys.path.insert(0, "/workspace/scripts")
        import conv_run
        wf_name = "7-qwen-2511-multiangle-auto" if lora == "multiangle" else "7-qwen-2511-multiangle-auto-batch"
        wf = json.load(open(f"/workspace/ComfyUI/user/default/workflows/{wf_name}.json"))
        for n in wf["nodes"]:
            if n["type"] == "CR Prompt Text":
                wv = n.get("widgets_values") or [""]
                if not str(wv[0]).strip().upper().startswith("<SKS"):
                    wv[0] = prompt_text  # instrucción de edición del usuario
            elif n["type"] == "LoadImage":
                (n.get("widgets_values") or [""])[0] = in_img or "example.png"
        api = conv_run.convert(wf)
        files = conv_run.run_api(api)
        for f in files:
            print(f)
        sys.exit(0 if files else 3)

    qwen_done = False
    if model == "qwen":
        # --- Qwen-Edit 2511 independiente: receta del workflow multi-ángulo ---
        # refs conditioning (describe la imagen) + Lightning 8 pasos @1.0 +
        # Kook @0.8 + 10 pasos cfg 1 euler/beta. LORA=lightning = rápido (4 pasos).
        pf = "qwen_img2img" if in_img else "qwen"
        wf = json.load(open(f"/workspace/payloads/{pf}.json"))
        if in_img:
            wf["5"]["inputs"]["image"] = in_img
        seed_node = "10" if in_img else "8"
        prefix_node = "12" if in_img else "10"
        prefix = "qwen_img2img" if in_img else "qwen"
        if lora == "lightning":
            # modo rápido: Lightning 4 pasos (fuerza 1.0) + 4 steps
            wf["4"]["inputs"]["lora_name"] = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors"
            wf[seed_node]["inputs"]["steps"] = 4
        if in_img:
            wf["8"]["inputs"]["prompt"] = prompt_text  # TextEncode con refs
        else:
            wf["5"]["inputs"]["text"] = prompt_text    # CLIPTextEncode
        qwen_done = True

    if not qwen_done:
        if model == "wan_i2v":
            # wan_i2v (14B, 2 etapas): full (20+20) o lightx2v (4+4, LORA=lightx2v)
            pf = "wan_i2v_fast" if lora == "lightx2v" else "wan_i2v"
            wf = json.load(open(f"/workspace/payloads/{pf}.json"))
            wf["7"]["inputs"]["image"] = in_img
            seed_node, prefix_node = "11", "15"
            prefix = "wan_i2v"
        elif model == "minimax_i2v":
            # MiniMax H3: payload autocontenido (i2v con audio nativo)
            wf = json.load(open("/workspace/payloads/minimax_i2v.json"))
            wf["5"]["inputs"]["image"] = in_img
            seed_node, prefix_node = "10", "15"
            prefix = "minimax_i2v"
        elif model == "krea2_moody":
            # mismo payload que krea2 pero con el base Moody y config del autor (euler_ancestral/beta, 8 pasos)
            pf = "krea2_img2img" if in_img else "krea2"
            wf = json.load(open(f"/workspace/payloads/{pf}.json"))
            if in_img:
                wf["7"]["inputs"]["image"] = in_img
            wf["1"]["inputs"]["unet_name"] = "moodyKrea2Mix_v60_fp8.safetensors"
            seed_node, prefix_node = ("10", "12") if in_img else ("8", "10")
            prefix = "krea2_moody" + ("_img2img" if in_img else "")
            for nid in (seed_node,):
                wf[nid]["inputs"]["sampler_name"] = "euler_ancestral"
                wf[nid]["inputs"]["scheduler"] = "beta"
        elif in_img:
            wf = json.load(open(f"/workspace/payloads/{model}_img2img.json"))
            wf["7"]["inputs"]["image"] = in_img
            seed_node, prefix_node = "10", "12"
            prefix = f"{model}_img2img"
        else:
            wf = json.load(open(f"/workspace/payloads/{model}.json"))
            seed_node, prefix_node = "8", "10"
            prefix = f"{model}"

    if not qwen_done:
        if model in ("wan_i2v", "minimax_i2v"):
            # payloads de video autocontenidos (sin LoRA): no tocar wiring
            pass
        elif lora == "krea_both" and model in ("krea2", "krea2_moody"):
            # COMBO: NSFW V4 + Realistic Snapshot encadenados (Krea 2 / Moody)
            s = float(os.environ.get("STRENGTH", 0.8 if in_img else 1.0))
            wf["4"]["inputs"].update({
                "lora_name": "Krea2NSFWV4.safetensors",
                "strength_model": s,
                "strength_clip": s,
            })
            wf["4b"] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": "RealisticSnapshotKrea2.safetensors",
                    "strength_model": 0.8,
                    "strength_clip": 0.8,
                    "model": ["4", 0],
                    "clip": ["4", 1],
                },
            }
            wf["5"]["inputs"]["clip"] = ["4b", 1]
            wf["6"]["inputs"]["clip"] = ["4b", 1]
            wf[seed_node]["inputs"]["model"] = ["4b", 0]
        elif lora_ok:
            wf["4"]["inputs"]["lora_name"] = lora_default
            default_strength = 0.65 if in_img else 0.8  # ediciones más suaves para respetar la foto original
            if model == "krea2":
                # autores: Realistic Snapshot 1.0->1.5->2.0, NSFW V4 0.8->1.2; img2img modera
                default_strength = 0.8 if in_img else 1.0
            wf["4"]["inputs"]["strength_model"] = float(os.environ.get("STRENGTH", default_strength))
            wf["4"]["inputs"]["strength_clip"] = float(os.environ.get("STRENGTH", default_strength))
            if model == "qwen" and lora == "lightning":
                # Lightning 4 pasos: LoRA de destilación a fuerza 1.0 + sampling turbo
                wf["4"]["inputs"]["strength_model"] = 1.0
                wf["4"]["inputs"]["strength_clip"] = 1.0
                wf[seed_node]["inputs"]["steps"] = 4
                wf[seed_node]["inputs"]["cfg"] = 1.0
        else:
            wf["5"]["inputs"]["clip"] = clip_src
            wf["6"]["inputs"]["clip"] = clip_src
            wf[seed_node]["inputs"]["model"] = model_src
            del wf["4"]

    if not qwen_done:
        wf["5"]["inputs"]["text"] = prompt_text
    wf[prefix_node]["inputs"]["filename_prefix"] = prefix
    wf[seed_node]["inputs"]["seed"] = int(time.time()) % 2**32

    resp = post("/prompt", {"prompt": wf, "client_id": "gen_cli"})
    pid = resp.get("prompt_id")
    if not pid:
        print("ERROR:", json.dumps(resp)[:300], file=sys.stderr)
        sys.exit(2)

    for _ in range(600):
        time.sleep(1)
        try:
            h = get("/history/" + pid)
        except Exception:
            continue
        if pid in h:
            out = h[pid].get("outputs", {})
            for node, data in out.items():
                for img in data.get("images", []) + data.get("videos", []):
                    p = os.path.join(BASE, img.get("type", "output"), img["subfolder"], img["filename"])
                    if os.path.exists(p):
                        print(p)
                        sys.exit(0)
            sys.exit(0)
    print("ERROR: timeout esperando output", file=sys.stderr)
    sys.exit(3)

if __name__ == "__main__":
    main()
