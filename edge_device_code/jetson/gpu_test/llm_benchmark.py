
import sys
import os
import time
import logging
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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_inference_test(use_gpu=False):
    """
    Runs a simple inference test and returns the time taken.
    """
    model_path = config.MODEL_PATH
    if not Path(model_path).exists():
        # Try resolving relative to jetson dir if not absolute
        possible_path = Path(__file__).resolve().parent.parent / model_path
        if possible_path.exists():
            model_path = str(possible_path)
        else:
            logging.error(f"Model file not found at {model_path}")
            return None

    gpu_layers = config.LLAMA_GPU_LAYERS if use_gpu else 0
    mode_name = "GPU" if use_gpu else "CPU"
    
    logging.info(f"Initializing model for {mode_name} (gpu_layers={gpu_layers})...")
    
    try:
        start_load = time.time()
        llm = Llama(
            model_path=model_path,
            n_threads=config.LLAMA_THREADS,
            n_ctx=config.LLAMA_CONTEXT,
            n_batch=config.LLAMA_BATCH,
            n_gpu_layers=gpu_layers,
            seed=config.LLAMA_SEED,
            verbose=False
        )
        load_time = time.time() - start_load
        logging.info(f"Model loaded in {load_time:.2f} seconds.")
        
        prompt = "User: Hello, tell me a short joke.\nAssistant:"
        logging.info(f"Generating response for prompt: '{prompt}'")
        
        start_gen = time.time()
        output = llm(
            prompt, 
            max_tokens=50, 
            stop=["User:", "\n"],
            echo=False
        )
        end_gen = time.time()
        
        gen_time = end_gen - start_gen
        response_text = output['choices'][0]['text'].strip()
        tokens_generated = output['usage']['completion_tokens']
        tokens_per_sec = tokens_generated / gen_time if gen_time > 0 else 0
        
        logging.info(f"[{mode_name}] Response: {response_text}")
        logging.info(f"[{mode_name}] Time: {gen_time:.4f}s, Tokens: {tokens_generated}, Speed: {tokens_per_sec:.2f} t/s")
        
        return {
            "mode": mode_name,
            "load_time": load_time,
            "gen_time": gen_time,
            "tokens": tokens_generated,
            "tps": tokens_per_sec
        }

    except Exception as e:
        logging.error(f"Failed to run {mode_name} test: {e}")
        return None

def main():
    print("=== Jetson LLM GPU vs CPU Benchmark ===")
    
    # 1. Run CPU Test
    print("\n--- Running CPU Test ---")
    cpu_result = run_inference_test(use_gpu=False)
    
    # 2. Run GPU Test
    print("\n--- Running GPU Test ---")
    gpu_result = run_inference_test(use_gpu=True)
    
    # 3. Compare
    print("\n=== Results Summary ===")
    if cpu_result:
        print(f"CPU Speed: {cpu_result['tps']:.2f} tokens/sec")
    else:
        print("CPU Test Failed")
        
    if gpu_result:
        print(f"GPU Speed: {gpu_result['tps']:.2f} tokens/sec")
    else:
        print("GPU Test Failed")
        
    if cpu_result and gpu_result and cpu_result['tps'] > 0:
        speedup = gpu_result['tps'] / cpu_result['tps']
        print(f"\nSpeedup: {speedup:.2f}x")

if __name__ == "__main__":
    main()
