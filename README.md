# Production-Grade Systematic Algorithmic Trading System

A high-performance, event-driven algorithmic trading system designed for the National Stock Exchange (NSE) of India. This system integrates real-time data ingestion, advanced feature engineering, multi-strategy execution, and rigorous risk management.

## Features

- **Live Data Ingestion**: Multi-broker support for Zerodha (Kite Connect) and Shoonya (NorenAPI) with automated reconnection and heartbeat monitoring.
- **Advanced Feature Engineering**: 
    - **Microstructure**: Order Flow Imbalance (OFI), VPIN, Bid-Ask Spread dynamics, Microprice.
    - **Options**: Real-time Greeks calculation (Delta, Gamma, Vega, Theta) and Implied Volatility (IV) surface analysis.
    - **Statistical**: Parkinson Volatility, Regime Detection via Hidden Markov Models (HMM), Momentum Z-scores.
- **Multi-Strategy Architecture**:
    - **StatArb**: Pairs trading with Kalman Filter-based dynamic hedge ratios and cointegration testing.
    - **ML Directional**: Directional predictors using LightGBM/ONNX models with Walk-Forward validation.
    - **Options RV**: Relative Value scanning for Volatility Arbitrage.
- **Execution Engine**: Smart order routing, slicing for large orders, and passive-to-market order upgrades.
- **Risk Management**:
    - **Pre-Trade**: Order-level limits, Buying Power checks, ADV-based volume limits.
    - **Real-Time**: Portfolio drawdown monitoring, exposure limits, and an automated Kill Switch.
- **Infrastructure**:
    - **ClickHouse**: Time-series storage for ticks, features, and trade logs.
    - **Kafka**: High-throughput message bus for inter-module communication.
    - **Redis**: Low-latency state management for live positions and features.
- **Monitoring**: Real-time Streamlit dashboard for PnL, risk utilization, and signal heatmaps.

## 🛠️ Tech Stack

- **Language**: Python 3.11+
- **Data Processing**: Polars, NumPy, SciPy
- **Machine Learning**: LightGBM, ONNX, Scikit-learn
- **Storage**: ClickHouse, Redis
- **Messaging**: Apache Kafka
- **Visualization**: Streamlit, Plotly

## 📂 Project Structure

```text
trading-system/
├── config/             # YAML configurations and secrets templates
├── core/               # Event bus, market clock, and base configurations
├── data/               # Ingestion feeds, quality validation, and storage writers
├── execution/          # Broker clients and smart execution logic
├── features/           # Feature pipeline and mathematical calculators
├── infra/              # Docker Compose for ClickHouse, Kafka, Redis
├── monitoring/         # Streamlit dashboard and alerting bots
├── research/           # Backtester engine and performance reporting
├── risk/               # Pre-trade and real-time risk managers
├── scripts/            # Bootstrap and backfill utilities
├── strategies/         # Strategy implementations (StatArb, ML, Options)
└── tests/              # Comprehensive unit and integration tests
```

## ⚙️ Getting Started (Step-by-Step)

Follow these steps to get the trading system up and running on your local machine.

### Step 1: Clone the Repository
```bash
git clone https://github.com/Algo-Ankit/TradeProject.git
cd TradeProject
```

### Step 2: Infrastructure Setup (Docker)
The system requires ClickHouse, Kafka, and Redis. We provide a bootstrap script to automate this:
```bash
# Ensure Docker Desktop is running
bash scripts/bootstrap.sh
```
*This script will start the containers, initialize the ClickHouse schema, and create the necessary Kafka topics.*

### Step 3: Python Environment Setup
We recommend using a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 4: Configuration & Secrets
1. Navigate to the `config/` directory.
2. Copy the template: `cp config/secrets.env.template config/secrets.env` (or manually rename it).
3. Open `config/secrets.env` and enter your credentials:
    - **Zerodha**: API Key, Secret, and Access Token.
    - **Shoonya**: User, Password, TOTP Secret, etc.
    - **Telegram**: Bot Token and Chat ID (for live alerts).

### Step 5: Data Backfilling (Historical Data)
Before running a backtest, you need some data in ClickHouse:
```bash
# Backfill NSE Bhavcopy data for the year 2024
python scripts/backfill.py --from 2024-01-01 --to 2024-12-31
```

## 📈 Launching the System

### 1. Run a Backtest
Validate the StatArb strategy on historical data:
```bash
python scripts/run_backtest.py --months 6 --symbols RELIANCE,TCS,INFY,ICICIBANK
```
*An HTML report will be generated in the `reports/` folder.*

### 2. Launch the Live Dashboard
Monitor PnL, positions, and signals in real-time:
```bash
streamlit run monitoring/dashboard/app.py
```

### 3. Execution & Strategy (Live/Paper)
Ensure `system.yaml` is configured for `paper_mode: true` for testing without real money.
```bash
# Standard entry point (implementation varies based on your main orchestrator)
# python main.py 
```

## 🛡️ License
Proprietary. Developed by [Ankit Anand Singh](https://github.com/Algo-Ankit).
