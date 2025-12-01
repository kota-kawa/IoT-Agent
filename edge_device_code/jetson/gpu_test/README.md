# Jetson GPU Benchmarks

This directory contains scripts to benchmark GPU vs CPU performance on the Jetson Edge Device.

## Scripts

### 1. `llm_benchmark.py`
This script benchmarks the **LLM inference speed** (tokens per second). It loads the model defined in `../jetson_config.py` and runs a short generation task twice:
- Once using only CPU (`n_gpu_layers=0`).
- Once using the GPU (`n_gpu_layers=16` or configured value).

**Usage:**
```bash
cd edge_device_code/jetson/gpu_test
python3 llm_benchmark.py
```
*Note: Requires the LLM model file to be present.*

### 2. `pytorch_benchmark.py`
This script benchmarks **raw matrix multiplication performance** using PyTorch. It compares the time taken to multiply two large matrices (4096 x 4096) on CPU vs CUDA.

**Usage:**
```bash
cd edge_device_code/jetson/gpu_test
python3 pytorch_benchmark.py
```
*Note: Requires PyTorch with CUDA support installed (standard on Jetson).*
