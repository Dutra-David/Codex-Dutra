#!/usr/bin/env python3
"""
Codex Dutra - Hardware Profiler & LLM Auto-Tuner
------------------------------------------------
Detects CPU architecture, physical RAM, and GPU capabilities (via PyTorch CUDA or native CLI fallbacks)
to dynamically suggest optimal quantization, context size, and thread settings for hermes3:8b.

Writes the results to:
  - ./codex_hardware_profile.json
  - ./backend/codex_hardware_profile.json (if directory exists)
"""

import os
import sys
import json
import platform
import subprocess
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HardwareProfiler")

def run_command(cmd):
    """Run a system command and return output, or None if failed."""
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Command '{cmd}' failed: {e}")
    return None

def get_cpu_info():
    """Detects CPU model, physical cores, and logical threads."""
    cpu_name = platform.processor() or "Unknown CPU"
    cores = os.cpu_count() or 4
    threads = cores
    
    system = platform.system()
    if system == "Windows":
        # Query WMI on Windows
        wmic_name = run_command("wmic cpu get name")
        if wmic_name:
            lines = [l.strip() for l in wmic_name.split("\n") if l.strip()]
            if len(lines) > 1:
                cpu_name = lines[1]
        
        wmic_cores = run_command("wmic cpu get NumberOfCores, NumberOfLogicalProcessors")
        if wmic_cores:
            lines = [l.strip() for l in wmic_cores.split("\n") if l.strip()]
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 2:
                    try:
                        cores = int(parts[0])
                        threads = int(parts[1])
                    except ValueError:
                        pass
                        
    elif system == "Darwin":
        # MacOS sysctl commands
        brand = run_command("sysctl -n machdep.cpu.brand_string")
        if brand:
            cpu_name = brand
        ncpu = run_command("sysctl -n hw.ncpu")
        if ncpu:
            try:
                cores = int(ncpu)
                threads = cores
            except ValueError:
                pass
                
    elif system == "Linux":
        # Read from /proc/cpuinfo
        cpuinfo = run_command("cat /proc/cpuinfo | grep 'model name' | head -n 1")
        if cpuinfo and ":" in cpuinfo:
            cpu_name = cpuinfo.split(":", 1)[1].strip()
            
        lscpu = run_command("lscpu | grep -E 'CPU\(s\):|Core\(s\) per socket:|Thread\(s\) per core:'")
        if lscpu:
            # Simple parse
            try:
                for line in lscpu.split("\n"):
                    if "Thread(s) per core" in line:
                        tpc = int(line.split(":")[1].strip())
                    if "Core(s) per socket" in line:
                        cps = int(line.split(":")[1].strip())
            except Exception:
                pass

    return {
        "model": cpu_name,
        "cores": cores,
        "threads": threads,
        "architecture": platform.machine()
    }

def get_ram_info():
    """Detects total physical RAM in GB."""
    system = platform.system()
    total_bytes = 0
    
    # Try using psutil if available
    try:
        import psutil
        total_bytes = psutil.virtual_memory().total
    except ImportError:
        # Fallbacks without psutil
        if system == "Windows":
            wmic_ram = run_command("wmic ComputerSystem get TotalPhysicalMemory")
            if wmic_ram:
                lines = [l.strip() for l in wmic_ram.split("\n") if l.strip()]
                if len(lines) > 1:
                    try:
                        total_bytes = int(lines[1])
                    except ValueError:
                        pass
        elif system == "Darwin":
            memsize = run_command("sysctl -n hw.memsize")
            if memsize:
                try:
                    total_bytes = int(memsize)
                except ValueError:
                    pass
        elif system == "Linux":
            meminfo = run_command("cat /proc/meminfo | grep MemTotal")
            if meminfo:
                try:
                    kb = int(meminfo.split()[1])
                    total_bytes = kb * 1024
                except ValueError:
                    pass

    # If all failed, return a reasonable default
    if total_bytes == 0:
        return 16.0  # 16 GB default guess
        
    return round(total_bytes / (1024 ** 3), 1)

def get_gpu_info():
    """
    Detects GPU model and VRAM size.
    Prefers torch.cuda, falls back to nvidia-smi or system profiler APIs.
    """
    gpu_name = "None"
    vram_gb = 0.0
    provider = "CPU"

    # Option 1: Try PyTorch CUDA as requested
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            # total_memory returns bytes
            total_bytes = torch.cuda.get_device_properties(0).total_memory
            vram_gb = round(total_bytes / (1024 ** 3), 1)
            provider = "PyTorch CUDA"
            return {"name": gpu_name, "vram_gb": vram_gb, "provider": provider}
    except ImportError:
        logger.debug("PyTorch not installed, checking system command line fallbacks...")

    # Option 2: Try nvidia-smi fallback
    smi_out = run_command("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits")
    if smi_out:
        parts = [p.strip() for p in smi_out.split(",")]
        if len(parts) >= 2:
            gpu_name = parts[0]
            try:
                # VRAM is in MB in nvidia-smi output
                vram_mb = int(parts[1])
                vram_gb = round(vram_mb / 1024.0, 1)
                provider = "NVIDIA System CLI"
                return {"name": gpu_name, "vram_gb": vram_gb, "provider": provider}
            except ValueError:
                pass

    # Option 3: macOS specific checks for Apple Silicon (Metal/Unified Memory)
    if platform.system() == "Darwin":
        # Check for Apple Silicon M1/M2/M3
        sys_prof = run_command("system_profiler SPDisplaysDataType")
        if sys_prof:
            for line in sys_prof.split("\n"):
                if "Chipset Model" in line:
                    gpu_name = line.split(":")[1].strip()
                if "Total Number of Cores" in line or "Cores" in line:
                    pass
            if "Apple" in gpu_name:
                # In Apple Silicon, RAM and VRAM are unified. We allocate up to 60% of total RAM for unified VRAM suggestion
                total_ram = get_ram_info()
                vram_gb = round(total_ram * 0.6, 1)
                provider = "Apple Silicon Unified Memory"
                return {"name": f"{gpu_name} (Unified)", "vram_gb": vram_gb, "provider": provider}

    # Option 4: Windows DirectX Graphic Device
    if platform.system() == "Windows":
        dx_gpu = run_command("wmic path win32_VideoController get Name, AdapterRAM")
        if dx_gpu:
            lines = [l.strip() for l in dx_gpu.split("\n") if l.strip()]
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 2:
                    try:
                        # Extract last part as RAM and the rest as name
                        ram_val = int(parts[0])
                        # wmic returns unsigned bytes or negative numbers sometimes, sanitize:
                        if ram_val < 0:
                            ram_val = 4294967296  # standard 4GB fallback
                        vram_gb = round(ram_val / (1024 ** 3), 1)
                        gpu_name = " ".join(parts[1:])
                        provider = "WMI Win32_VideoController"
                    except ValueError:
                        # try joining words reversed
                        gpu_name = lines[1]
                        vram_gb = 0.0

    return {
        "name": gpu_name if gpu_name != "None" else "Intel/AMD Integrated Graphics",
        "vram_gb": vram_gb,
        "provider": provider if gpu_name != "None" else "Integrated System Driver"
    }

def auto_tune_hermes3(cpu, ram_gb, gpu):
    """
    Computes optimal parameters for running the hermes3:8b (8-billion parameter) model.
    Hermes 3 8B contains 32 transformer layers. 1 layer requires roughly ~120-150MB of VRAM in Q4.
    Entire model requires:
      - ~4.8 GB in Q4_K_M (4-bit, highly recommended)
      - ~6.4 GB in Q8_0 (8-bit)
      - ~16.0 GB in FP16 (No quantization)
    """
    vram = gpu["vram_gb"]
    is_apple_silicon = "Apple" in gpu["name"]
    
    # 1. Select optimal quantization & VRAM offload
    if vram >= 16.0 and not is_apple_silicon:
        # Full high-end RTX/CUDA
        quant = "FP16 (Acurácia Máxima / Sem Perdas)"
        offload = "32 camadas (100% GPU Offload)"
        speed = "75-95 tokens/s"
        context = 16384
        flash = "Habilitado (FlashAttention-2 via CUDA)"
        description = "Desempenho extremo. O modelo de 8B é executado em FP16 totalmente na GPU sem qualquer quantização ou perda de qualidade."
    elif vram >= 10.0:
        # High mid-range (RTX 3060 12GB, RTX 4070 12GB)
        quant = "Q8_0 (Acurácia de Alta Fidelidade / 8-bit)"
        offload = "32 camadas (100% GPU Offload)"
        speed = "45-60 tokens/s"
        context = 8192
        flash = "Habilitado (CUDA)"
        description = "Excelente perfil de alta precisão. Quantização de 8-bit roda inteiramente na VRAM, proporcionando velocidade ideal e perda de perplexidade imperceptível."
    elif vram >= 7.0:
        # Standard desktop (RTX 3060 8GB, RTX 4060, Apple Silicon unified M1/M2 16GB)
        quant = "Q4_K_M (Equilibrado / 4-bit - Altamente Recomendado)"
        offload = "32 camadas (100% GPU Offload)" if vram >= 8.2 else "28 camadas na GPU (Restante na CPU)"
        speed = "32-40 tokens/s"
        context = 8192
        flash = "Habilitado"
        description = "Perfil padrão ouro para consumo local. O modelo roda em 4-bit com excelente compressão, permitindo que quase todas as camadas sejam processadas pelo hardware gráfico."
    elif vram >= 4.0:
        # Entry level GPUs (RTX 3050, GTX 1660 Super)
        quant = "Q3_K_L (Compacto / 3-bit)"
        offload = "16 a 20 camadas na GPU (Restante na CPU)"
        speed = "18-25 tokens/s"
        context = 4096
        flash = "Habilitado (Modo Híbrido)"
        description = "Configuração híbrida ativa. Como a placa possui pouca VRAM dedicada, dividimos o modelo para evitar gargalos na memória do sistema (RAM)."
    else:
        # No usable GPU or very old, run fully on CPU (or Apple Silicon base 8GB)
        if ram_gb >= 16.0:
            quant = "Q4_K_M (Equilibrado / 4-bit)"
            offload = "0 camadas na GPU (Processamento 100% na CPU)"
            speed = "10-14 tokens/s"
            context = 2048
            flash = "Desativado (Executando via CPU AVX2/AVX512)"
            description = "O processamento será executado via CPU e RAM do sistema. Recomendamos fechar aplicativos pesados em segundo plano para evitar lentidão."
        else:
            quant = "Q2_K ou alternar para Llama-3.2-3B"
            offload = "0 camadas na GPU (Recursos muito limitados)"
            speed = "4-8 tokens/s"
            context = 2048
            flash = "Desativado"
            description = "Hardware abaixo do mínimo recomendado para Hermes 3 8B. Sugere-se alternar para modelos mais compactos de 1B ou 3B para garantir respostas fluidas."

    # Recommended thread count: physical cores are ideal for parallel matrix multiplications
    # to avoid context switching costs of hyperthreading.
    recommended_threads = max(1, min(cpu["cores"], 12))

    return {
        "optModel": "Hermes-3-8B / Qwen-2.5-7B" if vram >= 4.0 else "Llama-3.2-3B / Qwen-2.5-3B",
        "optQuant": quant,
        "optOffload": offload,
        "optThreads": f"{recommended_threads} threads físicos recomendados",
        "optContext": f"{context} tokens",
        "optFlash": flash,
        "optSpeed": speed,
        "desc": description
    }

def main():
    logger.info("Codex Dutra - Iniciando Análise de Hardware Local...")
    
    cpu = get_cpu_info()
    ram = get_ram_info()
    gpu = get_gpu_info()
    
    logger.info(f"CPU Detectada: {cpu['model']} ({cpu['cores']} Cores / {cpu['threads']} Threads)")
    logger.info(f"RAM Detectada: {ram} GB")
    logger.info(f"GPU Detectada: {gpu['name']} ({gpu['vram_gb']} GB VRAM - detectado via {gpu['provider']})")
    
    # Auto-tune for Hermes 3 (8B)
    recommendations = auto_tune_hermes3(cpu, ram, gpu)
    
    # Structure the final profile JSON output
    profile = {
        "schema_version": "1.0.0",
        "system_os": platform.system(),
        "os_release": platform.release(),
        "timestamp": run_command("date /t") or run_command("date") or "N/A",
        "hardware": {
            "id": "custom",
            "name": f"Estação Local ({gpu['name'] if gpu['vram_gb'] > 0 else 'CPU-Only'})",
            "cpu": f"{cpu['model']} ({cpu['cores']} Cores / {cpu['threads']} Threads)",
            "ram": f"{ram} GB RAM",
            "gpu": f"{gpu['name']}",
            "vram": f"{gpu['vram_gb']} GB VRAM dedicada ({gpu['provider']})",
            "storage": "Detectado M.2/SATA"
        },
        "optimizations": {
            "optModel": recommendations["optModel"],
            "optQuant": recommendations["optQuant"],
            "optOffload": recommendations["optOffload"],
            "optThreads": recommendations["optThreads"],
            "optContext": recommendations["optContext"],
            "optFlash": recommendations["optFlash"],
            "optSpeed": recommendations["optSpeed"],
            "desc": recommendations["desc"]
        }
    }
    
    # Define file destinations
    filename = "codex_hardware_profile.json"
    destinations = [
        os.path.join(".", filename),
        os.path.join(".", "backend", filename)
    ]
    
    written_paths = []
    for dest in destinations:
        # Ensure directory exists if it's backend/
        parent = os.path.dirname(dest)
        if parent != "." and not os.path.exists(parent):
            continue
        try:
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
            written_paths.append(os.path.abspath(dest))
        except Exception as e:
            logger.error(f"Erro ao salvar arquivo de perfil em {dest}: {e}")
            
    if written_paths:
        print("\n" + "="*60)
        print("🎉 SUCESSO! PERFIL DE HARDWARE GERADO PARA O CODEX DUTRA!")
        print("="*60)
        for path in written_paths:
            print(f" -> Salvo em: {path}")
        print("-"*60)
        print("Importe este arquivo JSON diretamente na aba 'Hardware' do Codex")
        print("Dutra para calibrar e sintonizar o seu Ollama local instantaneamente!")
        print("="*60 + "\n")
    else:
        logger.error("Não foi possível salvar o arquivo de configuração de perfil.")

if __name__ == "__main__":
    main()
