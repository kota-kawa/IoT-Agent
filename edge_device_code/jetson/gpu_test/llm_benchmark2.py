# /mnt/data/llm_benchmark.py

import argparse
import logging
import os
import sys
import time
from pathlib import Path


# Add parent directory to path to import jetson_config
sys.path.append(str(Path(__file__).resolve().parent.parent))

try:
    import jetson_config as config
    from llama_cpp import Llama
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Please run this script from the edge_device_code/jetson/gpu_test directory.")
    sys.exit(1)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def resolve_model_path(raw_path: str) -> str:
    """Resolve model path relative to this directory if needed."""
    model_path = Path(raw_path)
    if model_path.exists():
        return str(model_path)

    alt_path = Path(__file__).resolve().parent.parent / raw_path
    if alt_path.exists():
        return str(alt_path)

    raise FileNotFoundError(f"Model file not found at {raw_path} or {alt_path}")


def build_prompt(model_path: str, user_prompt: str):
    """Return a prompt string and stop tokens tuned for the detected model family."""
    name = Path(model_path).name.lower()

    if "qwen" in name:
        prompt = (
            "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
            f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        stop = ["<|im_end|>"]
    else:
        prompt = f"User: {user_prompt}\nAssistant:"
        stop = ["User:", "\nUser:"]

    return prompt, stop


def build_gpu_candidates(requested: int) -> list[int]:
    """Create a descending list of gpu layer candidates to try when memory is tight."""
    if requested == 0:
        return [0]

    if requested == -1:
        # Start with full offload and back off aggressively for Jetson memory limits
        candidates = [-1, 48, 40, 32, 24, 16, 8, 4, 1]
    else:
        step = max(1, requested // 2)
        candidates = [requested]
        current = requested - step
        while current > 0:
            candidates.append(current)
            current -= step
        candidates.append(0)

    # Remove duplicates while preserving order
    seen = set()
    ordered = []
    for value in candidates:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def load_model(model_path: str, gpu_layers: int, args: argparse.Namespace):
    """Attempt to load the model with the given offload setting."""
    logging.info("Initializing model for %s (gpu_layers=%s)...", "GPU" if gpu_layers else "CPU", gpu_layers)
    start = time.time()
    llm = Llama(
        model_path=model_path,
        n_threads=args.threads,
        n_ctx=args.context,
        n_batch=args.batch,
        n_gpu_layers=gpu_layers,
        seed=args.seed,
        verbose=False,
    )
    load_time = time.time() - start
    logging.info("Model loaded in %.2f seconds.", load_time)
    return llm, load_time


def run_inference(model_path: str, gpu_layers: int, prompt: str, stop: list[str], args: argparse.Namespace):
    """
    Load the model and run a single inference.

    ※ この関数は 1 回のベンチマーク試行を表す。
       （試行回数を増やしたい場合は main() 側でこの関数をループする）
    """
    mode_name = "GPU" if gpu_layers else "CPU"

    try:
        llm, load_time = load_model(model_path, gpu_layers, args)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to load model for %s: %s", mode_name, exc)
        return None

    try:
        logging.info("Generating response for prompt: '%s'", prompt.replace("\n", "\\n"))
        start_gen = time.time()
        output = llm(
            prompt,
            max_tokens=args.max_tokens,
            stop=stop,
            echo=False,
            temperature=args.temperature,
        )
        gen_time = time.time() - start_gen

        response_text = output["choices"][0]["text"].strip()
        completion_tokens = output.get("usage", {}).get("completion_tokens")
        if completion_tokens is None:
            completion_tokens = len(response_text.split())
        tokens_per_sec = completion_tokens / gen_time if gen_time > 0 else 0

        logging.info("[%s] Response: %s", mode_name, response_text)
        logging.info("[%s] Time: %.4fs, Tokens: %s, Speed: %.2f t/s", mode_name, gen_time, completion_tokens, tokens_per_sec)

        return {
            "mode": mode_name,
            "load_time": load_time,
            "gen_time": gen_time,
            "tokens": completion_tokens,
            "tps": tokens_per_sec,
            "gpu_layers": gpu_layers,
        }
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to run %s test: %s", mode_name, exc)
        return None


def run_gpu_with_fallback(model_path: str, prompt: str, stop: list[str], args: argparse.Namespace):
    """Try GPU inference with decreasing offload until it fits in memory."""
    for candidate in build_gpu_candidates(args.gpu_layers):
        result = run_inference(model_path, candidate, prompt, stop, args)
        if result:
            if candidate != args.gpu_layers:
                logging.warning("GPU offload reduced from %s to %s due to previous errors.", args.gpu_layers, candidate)
            return result
    logging.error("GPU benchmark failed for all tested gpu_layers settings.")
    return None


def parse_args():
    parser = argparse.ArgumentParser(description="Jetson LLM GPU vs CPU benchmark")
    parser.add_argument("--model", default=config.MODEL_PATH, help="Path to GGUF model (default: config.MODEL_PATH)")
    parser.add_argument("--gpu-layers", type=int, default=config.LLAMA_GPU_LAYERS, help="Layers to offload to GPU (-1 = all)")
    parser.add_argument("--threads", type=int, default=config.LLAMA_THREADS, help="CPU threads")
    parser.add_argument("--context", type=int, default=config.LLAMA_CONTEXT, help="Context window")
    parser.add_argument("--batch", type=int, default=config.LLAMA_BATCH, help="Batch size")
    parser.add_argument("--seed", type=int, default=config.LLAMA_SEED, help="RNG seed")
    parser.add_argument("--temperature", type=float, default=config.LLAMA_TEMPERATURE, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, default=50, help="Tokens to generate for the benchmark")
    parser.add_argument("--user-prompt", default="Hello, tell me a short joke.", help="User message for the test")
    parser.add_argument("--cpu-only", action="store_true", help="Skip GPU test")
    parser.add_argument("--gpu-only", action="store_true", help="Skip CPU test")

    # ★ 追加: 試行回数オプション
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Number of times to repeat each benchmark (default: 1)",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # 念のため 1 未満を指定された場合は 1 に矯正
    trials = max(1, args.trials)

    try:
        model_path = resolve_model_path(args.model)
    except FileNotFoundError as exc:
        logging.error(str(exc))
        sys.exit(1)

    prompt, stop = build_prompt(model_path, args.user_prompt)

    print("=== Jetson LLM GPU vs CPU Benchmark ===")

    cpu_results = []
    gpu_results = []

    # --- CPU テスト ---
    if not args.gpu_only:
        for i in range(trials):
            print(f"\n--- Running CPU Test (trial {i + 1}/{trials}) ---")
            result = run_inference(model_path, 0, prompt, stop, args)
            if result:
                cpu_results.append(result)

    # --- GPU テスト ---
    if not args.cpu_only:
        for i in range(trials):
            print(f"\n--- Running GPU Test (trial {i + 1}/{trials}) ---")
            result = run_gpu_with_fallback(model_path, prompt, stop, args)
            if result:
                gpu_results.append(result)

    # --- 結果サマリ ---
    print("\n=== Results Summary ===")

    # CPU 平均
    cpu_result = None
    if cpu_results:
        avg_cpu_tps = sum(r["tps"] for r in cpu_results) / len(cpu_results)
        cpu_result = {
            "tps": avg_cpu_tps,
        }
        print(f"CPU Speed (avg over {len(cpu_results)} runs): {avg_cpu_tps:.2f} tokens/sec")
    elif not args.gpu_only:
        print("CPU Test Failed")

    # GPU 平均
    gpu_result = None
    if gpu_results:
        avg_gpu_tps = sum(r["tps"] for r in gpu_results) / len(gpu_results)
        used_layers = {r.get("gpu_layers") for r in gpu_results}
        gpu_result = {
            "tps": avg_gpu_tps,
        }

        # 実際に使われた gpu_layers 情報を表示
        if len(used_layers) == 1:
            layers_info = next(iter(used_layers))
            print(
                f"GPU Speed (avg over {len(gpu_results)} runs): "
                f"{avg_gpu_tps:.2f} tokens/sec (gpu_layers={layers_info})"
            )
        else:
            layers_list = sorted(l for l in used_layers if l is not None)
            print(
                f"GPU Speed (avg over {len(gpu_results)} runs): "
                f"{avg_gpu_tps:.2f} tokens/sec (gpu_layers used: {layers_list})"
            )
    elif not args.cpu_only:
        print("GPU Test Failed")

    # Speedup 計算（CPU / GPU 両方成功した場合）
    if cpu_result and gpu_result and cpu_result["tps"] > 0:
        speedup = gpu_result["tps"] / cpu_result["tps"]
        print(f"\nSpeedup: {speedup:.2f}x")


if __name__ == "__main__":
    main()
