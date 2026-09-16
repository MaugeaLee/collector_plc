#!/usr/bin/env bash
# collector-plc 를 systemd 서비스로 설치한다.
# 프로젝트에 이미 있는 .venv 를 그대로 복사해 사용한다. (새로 만들지 않음)
# 사용: sudo ./service_add.sh
set -euo pipefail

SERVICE_NAME="collector-plc"
INSTALL_DIR="/opt/collector-plc"
CONFIG_DIR="/etc/collector-plc"
SERVICE_USER="collector"
SERVICE_GROUP="collector"
UNIT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_FILE="${UNIT_SRC}/systemd/${SERVICE_NAME}.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_PYTHON="${UNIT_SRC}/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "root 권한이 필요합니다. 예: sudo ./service_add.sh" >&2
  exit 1
fi

if [[ ! -f "${UNIT_FILE}" ]]; then
  echo "유닛 파일이 없습니다: ${UNIT_FILE}" >&2
  exit 1
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "프로젝트 .venv 가 없습니다: ${VENV_PYTHON}" >&2
  echo "먼저 프로젝트에서 venv 를 준비한 뒤 다시 실행하세요." >&2
  exit 1
fi

echo "[1/6] 시스템 사용자/그룹 확인 (${SERVICE_USER})"
if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${SERVICE_GROUP}" --home-dir "${INSTALL_DIR}" \
    --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
# Modbus RTU 시리얼 접근
if getent group dialout >/dev/null; then
  usermod -aG dialout "${SERVICE_USER}" || true
fi

echo "[2/6] 설치 디렉터리 동기화 (코드 + .venv) → ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
# 이전에 실패한 빈 .venv 가 있으면 지우고 원본으로 덮어쓴다
if [[ -d "${INSTALL_DIR}/.venv" ]] && [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  rm -rf "${INSTALL_DIR}/.venv"
fi
rsync -a --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'plc_setting.json' \
  --exclude '.cursor/' \
  --exclude 'docs/' \
  "${UNIT_SRC}/" "${INSTALL_DIR}/"

if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  echo "동기화 후 .venv/bin/python 이 없습니다: ${INSTALL_DIR}/.venv" >&2
  exit 1
fi

echo "[3/6] 설정 디렉터리 → ${CONFIG_DIR}"
mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_DIR}/.env" ]]; then
  if [[ -f "${UNIT_SRC}/.env.example" ]]; then
    cp "${UNIT_SRC}/.env.example" "${CONFIG_DIR}/.env"
    echo "  .env 생성: ${CONFIG_DIR}/.env  (값을 현장에 맞게 수정하세요)"
  else
    touch "${CONFIG_DIR}/.env"
    echo "  빈 .env 생성: ${CONFIG_DIR}/.env"
  fi
else
  echo "  기존 .env 유지: ${CONFIG_DIR}/.env"
fi

# 앱은 WorkingDirectory 기준 상대경로로 plc_setting.json 을 읽는다.
# 운영 설정은 /etc 에 두고, 설치 트리에는 심링크로 연결한다.
if [[ ! -f "${CONFIG_DIR}/plc_setting.json" ]]; then
  if [[ -f "${UNIT_SRC}/plc_setting.json" ]]; then
    cp "${UNIT_SRC}/plc_setting.json" "${CONFIG_DIR}/plc_setting.json"
  elif [[ -f "${UNIT_SRC}/plc_setting.example.json" ]]; then
    cp "${UNIT_SRC}/plc_setting.example.json" "${CONFIG_DIR}/plc_setting.json"
    echo "  plc_setting.json 생성(예시 복사). 현장 주소로 수정하세요."
  else
    echo "plc_setting.json / example 이 없습니다." >&2
    exit 1
  fi
else
  echo "  기존 plc_setting.json 유지: ${CONFIG_DIR}/plc_setting.json"
fi

ln -sfn "${CONFIG_DIR}/plc_setting.json" "${INSTALL_DIR}/plc_setting.json"
ln -sfn "${CONFIG_DIR}/.env" "${INSTALL_DIR}/.env"

echo "[4/6] 소유권"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${CONFIG_DIR}"
chmod 750 "${CONFIG_DIR}"
chmod 640 "${CONFIG_DIR}/.env" "${CONFIG_DIR}/plc_setting.json"

echo "[5/6] systemd 유닛 설치"
cp "${UNIT_FILE}" "${UNIT_DST}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

echo "[6/6] 서비스 시작"
systemctl restart "${SERVICE_NAME}.service"
systemctl --no-pager --full status "${SERVICE_NAME}.service" || true

cat <<EOF

설치 완료.

  유닛:     ${UNIT_DST}
  코드:     ${INSTALL_DIR}
  설정:     ${CONFIG_DIR}/.env
            ${CONFIG_DIR}/plc_setting.json

유용한 명령:
  sudo systemctl status ${SERVICE_NAME}
  sudo journalctl -u ${SERVICE_NAME} -f
  sudo systemctl restart ${SERVICE_NAME}

EOF
