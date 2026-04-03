# SSL 证书生成脚本（自签名，生产环境请替换为正式证书）
# 用法：./generate-self-signed-cert.sh

#!/bin/bash

SSL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ssl"
mkdir -p "$SSL_DIR"

echo "🔐 生成自签名 SSL 证书..."

# 生成私钥
openssl genrsa -out "$SSL_DIR/privkey.pem" 2048

# 生成证书签名请求
openssl req -new -key "$SSL_DIR/privkey.pem" \
    -out "$SSL_DIR/cert.csr" \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=MiniMem/CN=localhost"

# 生成自签名证书
openssl x509 -req -days 365 \
    -in "$SSL_DIR/cert.csr" \
    -signkey "$SSL_DIR/privkey.pem" \
    -out "$SSL_DIR/fullchain.pem"

# 清理
rm -f "$SSL_DIR/cert.csr"

echo "✅ 证书生成完成："
echo "   - 私钥：$SSL_DIR/privkey.pem"
echo "   - 证书：$SSL_DIR/fullchain.pem"
echo ""
echo "⚠️  注意：这是自签名证书，仅用于测试。"
echo "   生产环境请使用 Let's Encrypt 或购买正式 SSL 证书。"
