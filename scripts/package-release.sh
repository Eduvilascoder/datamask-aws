#!/usr/bin/env bash
#
# package-release.sh — Genera un tarball entregable de DataMask AWS para
# enviar a un partner o cliente. Excluye artefactos pesados y secretos.
#
# Uso:
#   ./scripts/package-release.sh [version]
#
# Salida: dist/datamask-aws-<version>.tar.gz
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(date +%Y%m%d)}"
OUT_DIR="$ROOT_DIR/dist"
ARCHIVE="$OUT_DIR/datamask-aws-${VERSION}.tar.gz"

mkdir -p "$OUT_DIR"

echo "→ Empaquetando DataMask AWS v$VERSION para entrega..."

# Excluir lo que el cliente regenera o no debe recibir (secretos, builds, deps).
tar --exclude-vcs \
  --exclude='./dist' \
  --exclude='./build' \
  --exclude='./frontend/build' \
  --exclude='./frontend/node_modules' \
  --exclude='./infra/node_modules' \
  --exclude='./infra/cdk.out' \
  --exclude='*/__pycache__' \
  --exclude='*/.pytest_cache' \
  --exclude='*.pyc' \
  --exclude='./frontend/.env.production' \
  --exclude='./frontend/.env.local' \
  --exclude='*.zip' \
  -czf "$ARCHIVE" -C "$ROOT_DIR" .

echo "✓ Entregable generado: $ARCHIVE"
echo "  Tamaño: $(du -h "$ARCHIVE" | cut -f1)"
echo ""
echo "  El cliente debe:"
echo "    1. Descomprimir: tar -xzf $(basename "$ARCHIVE")"
echo "    2. Leer docs/howtodeploydatamask.md"
echo "    3. Ejecutar: ./scripts/install.sh --profile <p> --alert-email <e> --callback-url <u>"
