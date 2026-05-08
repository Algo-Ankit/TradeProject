#!/bin/bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Trading System Bootstrap ===${NC}"

# 1. Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}ERROR: Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker found: $(docker --version)${NC}"

# 2. Check docker compose
if ! docker compose version &> /dev/null 2>&1; then
    echo -e "${RED}ERROR: docker compose plugin not found.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker Compose found: $(docker compose version)${NC}"

# 3. Start services
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR/infra"

echo -e "${YELLOW}Starting services...${NC}"
docker compose up -d

# 4. Wait for ClickHouse
echo -e "${YELLOW}Waiting for ClickHouse...${NC}"
for i in $(seq 1 30); do
    if curl -s http://localhost:8123/ping > /dev/null 2>&1; then
        echo -e "${GREEN}✓ ClickHouse ready${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}ERROR: ClickHouse did not start in time.${NC}"
        exit 1
    fi
    sleep 2
done

# 5. Run init.sql
echo -e "${YELLOW}Initializing ClickHouse schema...${NC}"
docker exec trading-clickhouse clickhouse-client --query "$(cat "$PROJECT_DIR/infra/clickhouse/init.sql")" || true
echo -e "${GREEN}✓ ClickHouse schema initialized${NC}"

# 6. Create Kafka topics
echo -e "${YELLOW}Creating Kafka topics...${NC}"
for topic in raw-ticks order-book features orders fills risk-events signals; do
    docker exec trading-kafka kafka-topics --create \
        --bootstrap-server localhost:9092 \
        --topic "$topic" \
        --partitions 4 \
        --replication-factor 1 \
        --if-not-exists 2>/dev/null || true
done
echo -e "${GREEN}✓ Kafka topics created${NC}"

# 7. Status
echo ""
echo -e "${YELLOW}Service Status:${NC}"
docker compose ps

# 8. Next steps
echo ""
echo -e "${GREEN}=== SETUP COMPLETE ===${NC}"
echo ""
echo "Next steps:"
echo "  1. Copy config/secrets.env.template to config/secrets.env and fill in API keys"
echo "  2. Install Python deps: pip install -r requirements.txt"
echo "  3. Backfill historical data: python scripts/backfill.py --from 2024-01-01 --to 2024-12-31"
echo "  4. Run backtest: python scripts/run_backtest.py"
echo "  5. Start dashboard: streamlit run monitoring/dashboard/app.py"
echo ""
echo "  ClickHouse UI: http://localhost:8123/play"
echo "  Redis CLI:     docker exec -it trading-redis redis-cli"
