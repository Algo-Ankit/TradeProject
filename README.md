# Systematic Algorithmic Trading Infrastructure (NSE India)

## Technical Specification & Operational Documentation

This repository contains a production-grade, low-latency algorithmic trading infrastructure optimized for the National Stock Exchange (NSE) of India. The system is architected as an event-driven distributed system, integrating institutional-grade risk controls, multi-asset quantitative strategies, and a high-fidelity research environment.

---

## 1. System Architecture

The infrastructure follows a decoupled, event-driven architecture designed for high throughput and fault tolerance.

### 1.1 Core Components
*   **Event Orchestration**: A central `EventBus` facilitates asynchronous communication between data feeds, strategy engines, and execution clients using `asyncio` for non-blocking I/O.
*   **Data Ingestion Layer**: Multi-threaded WebSocket consumers for Zerodha (Kite) and Shoonya (Noren), featuring automated failover and tick-level quality validation.
*   **Persistence Layer**: 
    *   **OLAP (ClickHouse)**: High-performance time-series storage for multi-year tick data, feature logs, and execution traces.
    *   **State (Redis)**: Low-latency in-memory store for real-time positions, feature states, and risk utilization metrics.
*   **Message Broker (Kafka)**: Decouples high-volume raw ticks from downstream feature computation and monitoring services.

### 1.2 Quantitative Framework
*   **Microstructure Features**: Real-time calculation of Order Flow Imbalance (OFI), Volume-Synchronized Probability of Informed Trading (VPIN), and Microprice dynamics.
*   **Options Engine**: High-performance Greeks calculation (Delta, Gamma, Vega, Theta) and IV surface calibration using Black-Scholes and Heston model abstractions.
*   **Regime Detection**: Statistical regime identification using Gaussian Hidden Markov Models (HMM) to adapt strategy parameters to market volatility states.

---

## 2. Risk Management & Compliance

The system implements a multi-layered safety framework to ensure capital preservation and regulatory compliance.

### 2.1 Pre-Trade Control Layer
Every signal must pass through the `PreTradeChecker` which validates:
*   **Order Size Limits**: Maximum INR value per clip.
*   **Liquidity Constraints**: Order-to-ADV (Average Daily Volume) percentage limits.
*   **Buying Power**: Real-time margin utilization checks against broker-reported capital.

### 2.2 Real-Time Risk Monitor
*   **Portfolio Drawdown**: Automated kill-switch activation if peak-to-trough drawdown exceeds defined thresholds.
*   **Exposure Management**: Gross and net exposure limits tracked across all sub-strategies.
*   **Kill Switch**: A global safety mechanism that cancels all pending orders and flattens active positions across all legs upon breach.

---

## 3. Execution Logic

The execution layer abstracts broker-specific APIs into a unified interface, supporting sophisticated routing logic.

*   **Smart Executor**: Implements order slicing (Time-Weighted or Volume-Weighted style) to minimize market impact.
*   **Passive-to-Active Upgrades**: Automated limit order management that upgrades to market execution if fill-latency exceeds specified timeouts.
*   **Slippage Profiling**: Detailed tracking of expected vs. realized fill prices to optimize execution parameters.

---

## 4. Research & Backtesting

The infrastructure includes a high-fidelity backtesting engine designed to eliminate look-ahead bias and simulate realistic market conditions.

*   **Engine**: Event-driven simulator that processes historical ClickHouse data as if it were a live feed.
*   **Fill Model**: Incorporates market impact (using Square-root impact models) and variable slippage based on depth-at-best.
*   **Validation**: Implementation of the **Deflated Sharpe Ratio (DSR)** to account for multiple testing bias and reduce the probability of false discoveries.
*   **Walk-Forward**: Automated runner for out-of-sample validation and parameter stability testing.

---

## 5. Operational Deployment

### 5.1 Prerequisites
*   Linux/Windows (Docker-enabled)
*   Python 3.11+
*   Kite Connect / Shoonya API Credentials
*   Minimum 8GB RAM for ClickHouse/Kafka stack

### 5.2 Infrastructure Initialization (The "Big Three")
The system relies on three primary distributed services. These are **fully containerized** via Docker—you do not need to install Redis, Kafka, or ClickHouse directly on your host machine.

Run the bootstrap script to pull and initialize the stack:
```powershell
./scripts/bootstrap.sh
```
This command manages the lifecycle of:
*   **Redis**: (Port 6379) - Used for real-time state and risk tracking.
*   **Kafka**: (Port 9092) - High-throughput tick distribution.
*   **ClickHouse**: (Port 8123/9000) - Analytical storage for historical research.

---

## 6. Execution Guide

This section provides the exact commands required to operate the system.

### 6.1 Data Preparation (ETL)
To populate the analytical database with historical NSE data for backtesting:
```bash
# Downloads and loads Bhavcopy data into ClickHouse
python scripts/backfill.py --from 2024-01-01 --to 2024-12-31
```

### 6.2 Research & Strategy Validation
To execute a backtest for the Statistical Arbitrage strategy:
```bash
# Runs the event-driven simulator and generates a performance report
python scripts/run_backtest.py --months 6 --symbols RELIANCE,TCS,HDFCBANK,INFY
```
*Output: `reports/backtest.html`*

### 6.3 Real-Time Monitoring
To launch the institutional-grade dashboard for PnL and Risk oversight:
```bash
# Requires Redis to be running via Step 5.2
streamlit run monitoring/dashboard/app.py
```

---

## 7. Directory Structure Overview

```text
trading-system/
├── core/               # System kernel: EventBus, MarketClock, AppConfig
├── data/               # Ingestion: Feed clients, Quality Validators, Storage Drivers
├── execution/          # OMS: Broker abstractions, Order Managers, Smart Exec
├── features/           # Alpha: Pipeline, Microstructure, Greeks, Stats
├── infra/              # DevOps: Docker, SQL Init, Kafka Config
├── monitoring/         # Ops: Telegram Alerters, Streamlit Dashboard
├── research/           # Analytics: Backtester, Performance Metrics, DSR Validation
├── risk/               # Safety: Pre-Trade, Real-Time Monitoring, Kill Switch
├── strategies/         # Quant: StatArb, ML Directional, Options RV
└── scripts/            # Utils: Bootstrap, Data Backfill, Backtest Runners
```

---

## 7. Development & Testing
Unit tests are mandatory for all core components. The suite covers data validation, mathematical greeks, and event-bus integrity.
```bash
pytest tests/
```

---
*Disclaimer: This software is for institutional research and trading purposes. Use at your own risk. The developers assume no liability for financial losses.*
