import time
import torch
import sys

def benchmark_matmul(device_name, size=4096):
    print(f"Preparing {device_name} benchmark (Matrix Size: {size}x{size})...")
    
    try:
        device = torch.device(device_name)
        
        # Create random matrices
        a = torch.randn(size, size, device=device)
        b = torch.randn(size, size, device=device)
        
        # Warm-up
        print(f"Warming up {device_name}...")
        _ = torch.matmul(a, b)
        if device_name == 'cuda':
            torch.cuda.synchronize()
            
        # Benchmark
        print(f"Running {device_name} benchmark...")
        start_time = time.time()
        
        # Perform matrix multiplication
        c = torch.matmul(a, b)
        
        if device_name == 'cuda':
            torch.cuda.synchronize()
            
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"{device_name} Time: {duration:.4f} seconds")
        return duration
        
    except Exception as e:
        print(f"Error during {device_name} benchmark: {e}")
        return None

def main():
    print("=== PyTorch CPU vs GPU Matrix Multiplication Benchmark ===")
    print(f"PyTorch Version: {torch.__version__}")
    
    if not torch.cuda.is_available():
        print("CUDA is not available on this system. Cannot run GPU benchmark.")
        return

    print(f"CUDA Device: {torch.cuda.get_device_name(0)}")
    
    matrix_size = 4096 # Adjustable size
    
    # CPU Test
    cpu_time = benchmark_matmul('cpu', size=matrix_size)
    
    # GPU Test
    gpu_time = benchmark_matmul('cuda', size=matrix_size)
    
    # Comparison
    if cpu_time and gpu_time:
        speedup = cpu_time / gpu_time
        print("\n=== Results ===")
        print(f"CPU Time: {cpu_time:.4f}s")
        print(f"GPU Time: {gpu_time:.4f}s")
        print(f"Speedup:  {speedup:.2f}x")

if __name__ == "__main__":
    main()
