#!/usr/bin/env bash
# =============================================================================
# DataMask AWS — Script de empaquetado de funciones Lambda
#
# Genera los artefactos ZIP necesarios para desplegar el stack CloudFormation:
#   - trigger.zip
#   - detection.zip
#   - redaction.zip
#   - api.zip
#   - pymupdf-layer.zip
#
# Uso:
#   ./scripts/package-lambdas.sh [--output-dir <dir>] [--skip-layer]
#
# Opciones:
#   --output-dir <dir>   Directorio de salida (default: ./build/lambdas)
#   --skip-layer         Omitir empaquetado del Lambda Layer PyMuPDF
# =============================================================================

set -euo pipefail

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Defaults
OUTPUT_DIR="./build/lambdas"
SKIP_LAYER=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LAMBDAS_DIR="${PROJECT_ROOT}/lambdas"

# Parse argumentos
while [[ $# -gt 0 ]]; do
  case $1 in
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --skip-layer)
      SKIP_LAYER=true
      shift
      ;;
    -h|--help)
      echo "Uso: $0 [--output-dir <dir>] [--skip-layer]"
      echo ""
      echo "Opciones:"
      echo "  --output-dir <dir>   Directorio de salida (default: ./build/lambdas)"
      echo "  --skip-layer         Omitir empaquetado del Lambda Layer PyMuPDF"
      exit 0
      ;;
    *)
      echo -e "${RED}Error: Opción desconocida: $1${NC}"
      exit 1
      ;;
  esac
done

echo "============================================================"
echo "  DataMask AWS — Empaquetado de funciones Lambda"
echo "============================================================"
echo ""
echo "  Proyecto:    ${PROJECT_ROOT}"
echo "  Salida:      ${OUTPUT_DIR}"
echo "  Skip layer:  ${SKIP_LAYER}"
echo ""

# Crear directorio de salida
mkdir -p "${OUTPUT_DIR}"

# Función para empaquetar una Lambda
package_lambda() {
  local name=$1
  local source_dir="${LAMBDAS_DIR}/${name}"
  local output_file="${OUTPUT_DIR}/${name}.zip"
  local tmp_dir

  echo -e "${YELLOW}→ Empaquetando ${name}...${NC}"

  if [ ! -d "${source_dir}" ]; then
    echo -e "${RED}  Error: No existe el directorio ${source_dir}${NC}"
    return 1
  fi

  tmp_dir=$(mktemp -d)
  trap "rm -rf ${tmp_dir}" RETURN

  # Copiar código fuente (solo archivos .py y requirements.txt)
  find "${source_dir}" -maxdepth 1 -name "*.py" -exec cp {} "${tmp_dir}/" \;

  # Copiar directorios de dependencias vendored (si existen)
  # La Lambda API ya tiene dependencias vendored en el directorio
  if [ "${name}" = "api" ]; then
    # Para la Lambda API, copiar todo excepto tests, __pycache__, .pytest_cache
    rsync -a --exclude='__pycache__' --exclude='.pytest_cache' --exclude='tests' \
      --exclude='*.pyc' "${source_dir}/" "${tmp_dir}/"
  else
    # Para otras Lambdas, instalar dependencias si hay requirements.txt
    if [ -f "${source_dir}/requirements.txt" ]; then
      pip install -q -r "${source_dir}/requirements.txt" -t "${tmp_dir}" \
        --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 \
        2>/dev/null || pip install -q -r "${source_dir}/requirements.txt" -t "${tmp_dir}" 2>/dev/null
    fi

    # Copiar dependencias vendored (como pypdf en trigger)
    for dir in "${source_dir}"/*/; do
      dir_name=$(basename "${dir}")
      if [[ "${dir_name}" != "__pycache__" && "${dir_name}" != ".pytest_cache" && "${dir_name}" != "tests" ]]; then
        if [ -d "${dir}" ]; then
          cp -r "${dir}" "${tmp_dir}/"
        fi
      fi
    done
  fi

  # Limpiar archivos innecesarios
  find "${tmp_dir}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "${tmp_dir}" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
  find "${tmp_dir}" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
  find "${tmp_dir}" -name "*.pyc" -delete 2>/dev/null || true
  find "${tmp_dir}" -name "*.pyo" -delete 2>/dev/null || true
  find "${tmp_dir}" -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true

  # Crear ZIP
  (cd "${tmp_dir}" && zip -qr "${output_file}" .)

  local size
  size=$(du -h "${output_file}" | cut -f1)
  echo -e "${GREEN}  ✓ ${name}.zip (${size})${NC}"
}

# Función para empaquetar el Lambda Layer de PyMuPDF
package_layer() {
  local output_file="${OUTPUT_DIR}/pymupdf-layer.zip"
  local tmp_dir
  local layer_source="${LAMBDAS_DIR}/layers/pymupdf"

  echo -e "${YELLOW}→ Empaquetando Lambda Layer PyMuPDF...${NC}"

  tmp_dir=$(mktemp -d)
  trap "rm -rf ${tmp_dir}" RETURN

  # Estructura requerida para Lambda Layer: python/
  mkdir -p "${tmp_dir}/python"

  if [ -d "${layer_source}" ] && [ "$(ls -A ${layer_source} 2>/dev/null)" ]; then
    # Si ya hay contenido precompilado, usarlo
    cp -r "${layer_source}"/* "${tmp_dir}/python/"
  else
    # Instalar PyMuPDF para la plataforma Lambda
    pip install -q pymupdf -t "${tmp_dir}/python" \
      --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 \
      2>/dev/null || {
        echo -e "${YELLOW}  ⚠ No se pudo instalar PyMuPDF para linux/x86_64.${NC}"
        echo -e "${YELLOW}    Instale con Docker: docker run --rm -v \$(pwd):/out python:3.12 pip install pymupdf -t /out/python${NC}"
        echo -e "${YELLOW}    Luego copie el directorio python/ a lambdas/layers/pymupdf/${NC}"
        return 1
      }
  fi

  # Limpiar archivos innecesarios del layer
  find "${tmp_dir}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "${tmp_dir}" -name "*.pyc" -delete 2>/dev/null || true
  find "${tmp_dir}" -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true

  # Crear ZIP
  (cd "${tmp_dir}" && zip -qr "${output_file}" .)

  local size
  size=$(du -h "${output_file}" | cut -f1)
  echo -e "${GREEN}  ✓ pymupdf-layer.zip (${size})${NC}"
}

# =============================================================================
# Ejecutar empaquetado
# =============================================================================

echo "─────────────────────────────────────────────────────────────"
echo " Empaquetando funciones Lambda..."
echo "─────────────────────────────────────────────────────────────"
echo ""

package_lambda "trigger"
package_lambda "detection"
package_lambda "redaction"
package_lambda "api"

if [ "${SKIP_LAYER}" = false ]; then
  echo ""
  echo "─────────────────────────────────────────────────────────────"
  echo " Empaquetando Lambda Layer..."
  echo "─────────────────────────────────────────────────────────────"
  echo ""
  package_layer
fi

echo ""
echo "============================================================"
echo -e "  ${GREEN}¡Empaquetado completado!${NC}"
echo "============================================================"
echo ""
echo "  Archivos generados en: ${OUTPUT_DIR}/"
echo ""
ls -lh "${OUTPUT_DIR}"/*.zip 2>/dev/null || true
echo ""
echo "  Siguiente paso: subir los ZIPs a S3"
echo ""
echo "    aws s3 sync ${OUTPUT_DIR}/ s3://<BUCKET>/datamask/lambdas/ --profile <PROFILE>"
echo ""
