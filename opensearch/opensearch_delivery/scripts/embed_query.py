"""Optional: embed a new question with BGE-M3, matching the existing document vectors."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="BAAI/bge-m3", help="Hugging Face ID or a local model directory")
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=args.local_only)
    model = AutoModel.from_pretrained(args.model, local_files_only=args.local_only)
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    tokens = tokenizer(args.text, return_tensors="pt", truncation=False)
    if tokens["input_ids"].shape[1] > 2048:
        raise ValueError("Question exceeds 2048 tokens; shorten it instead of silently truncating")
    with torch.inference_mode():
        result = model(**{key: value.to(device) for key, value in tokens.items()}).last_hidden_state[:, 0]
        result = torch.nn.functional.normalize(result, p=2, dim=1)
    vector = result.cpu().numpy().astype(np.float32)
    if vector.shape != (1, 1024) or not np.isfinite(vector).all():
        raise ValueError("Model output does not match the 1024-dimensional corpus")
    if args.output.suffix != ".npy":
        raise ValueError("--output must end with .npy")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.output, vector, allow_pickle=False)
    print(f"Saved one 1024-dimensional vector to {args.output}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
