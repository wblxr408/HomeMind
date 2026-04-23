#!/usr/bin/env bash
set -euo pipefail
export CONDA_NO_PLUGINS="true"
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

MODE="simulated"
HOST="0.0.0.0"
PORT="5000"
DEBUG="false"
SKIP_INSTALL="false"
CONDA_ENV="used_pytorch"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-simulated}"
      shift 2
      ;;
    --host)
      HOST="${2:-0.0.0.0}"
      shift 2
      ;;
    --port)
      PORT="${2:-5000}"
      shift 2
      ;;
    --debug)
      DEBUG="true"
      shift
      ;;
    --skip-install)
      SKIP_INSTALL="true"
      shift
      ;;
    --conda-env)
      CONDA_ENV="${2:-used_pytorch}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

cd "$(dirname "$0")"

activate_conda() {
  if ! command -v conda >/dev/null 2>&1; then
    echo "conda command not found, continuing with current Python environment."
    return
  fi

  echo
  echo "Activating conda environment: ${CONDA_ENV}"

  local conda_base
  conda_base="$(conda info --base 2>/dev/null || true)"
  if [[ -n "$conda_base" && -f "$conda_base/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "$conda_base/etc/profile.d/conda.sh"
    conda activate "$CONDA_ENV"
  else
    echo "conda shell hook unavailable, continuing with current Python environment."
  fi
}

install_requirements() {
  if [[ "$SKIP_INSTALL" == "true" ]]; then
    echo "Skipping dependency installation."
    return
  fi

  if [[ -f "./requirements-web.txt" ]]; then
    echo "Installing/updating web dependencies from requirements-web.txt..."
    python -m pip install -q -r ./requirements-web.txt
  else
    echo "requirements-web.txt not found, skipping dependency installation."
  fi
}

activate_conda
install_requirements

if [[ -z "${HOMEMIND_EMBEDDING_MODE:-}" ]]; then
  export HOMEMIND_EMBEDDING_MODE="local"
fi

echo
echo "========================================"
echo "   HomeMind Central Controller"
echo "========================================"
echo
echo "  Current Python:"
python --version
echo
echo "  Launch Config:"
echo "    - Mode:           ${MODE}"
echo "    - Host:           ${HOST}"
echo "    - Port:           ${PORT}"
echo "    - Debug:          ${DEBUG}"
echo "    - Embedding:      ${HOMEMIND_EMBEDDING_MODE}"
echo
echo "  Access URLs:"
echo "    - Control Panel:  http://localhost:${PORT}"
echo "    - API Status:     http://localhost:${PORT}/api/status"
echo "    - TAP Rules:      http://localhost:${PORT}/api/tap-rules"
echo "    - Floor Plans:    http://localhost:${PORT}/api/floor-plans"
echo
echo "  Tip: set HOMEMIND_EMBEDDING_MODE=download before startup if you want to allow model download/loading."
echo "  Press Ctrl+C to stop"
echo "========================================"
echo

ARGS=(--mode "$MODE" --host "$HOST" --port "$PORT")
if [[ "$DEBUG" == "true" ]]; then
  ARGS+=(--debug)
fi

python ./run_web.py "${ARGS[@]}"
