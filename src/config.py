import os
from pathlib import Path

ROOT = Path(__file__).parent.parent

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4.1-mini")
API_KEY = os.getenv("API_KEY", "").strip().rstrip(",")
BASE_URL = os.getenv("BASE_URL", "").strip().rstrip(",")

BENCHMARK_PATH = ROOT / "docs" / "test" / "xltc" / "sample_test.jsonl"
HARNESS_DIR = ROOT / "configs" / "harness"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"

HELD_IN_IDS_FILE = ROOT / "configs" / "benchmarks" / "held_in.yaml"
HELD_OUT_IDS_FILE = ROOT / "configs" / "benchmarks" / "held_out.yaml"
