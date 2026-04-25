# langgraph_orchestrator.py
import logging
logger = logging.getLogger("RiskRadar_Insights")
from typing import Dict, List, TypedDict, Annotated, Optional
try:
    from langchain_litellm import ChatLiteLLM
    logger.info("Using langchain_litellm package")
except ImportError:
    from langchain_community.chat_models import ChatLiteLLM
    logger.warning("Using deprecated langchain_community.chat_models - please install langchain-litellm")
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
import operator
from datetime import datetime, timezone, timedelta
import time
import json
import uuid
import requests
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn
from sqlalchemy import create_engine, text 
import pandas as pd

# --- NEW IMPORTS FOR SSH TUNNEL ---
from sshtunnel import SSHTunnelForwarder
import subprocess
import shutil
import signal
import warnings
from cryptography.utils import CryptographyDeprecationWarning
from urllib.parse import quote_plus

# Suppress warnings
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

# Load environment variables
load_dotenv()

# --- Start Logging Configuration ---
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper() # Allow configuring log level via env
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# --- logger --- 
logger = logging.getLogger("RiskRadar_Insights")
logger.setLevel(LOG_LEVEL)

# --- SSH TUNNEL CONFIGURATION ---
SSH_HOST = os.getenv("SSH_HOST", "13.200.109.217")
SSH_PORT = int(os.getenv("SSH_PORT", "22"))
SSH_USER = os.getenv("SSH_USER", "api-team")
SSH_PKEY_PATH = os.getenv("SSH_PKEY_PATH", "")  
SSH_PASSWORD = os.getenv("SSH_PASSWORD")  

REMOTE_DB_HOST = os.getenv("REMOTE_DB_HOST", "transbnk-prod.cqlu0rb6gccj.ap-south-1.rds.amazonaws.com")
REMOTE_DB_PORT = int(os.getenv("REMOTE_DB_PORT", "3974"))
LOCAL_BIND_HOST = os.getenv("LOCAL_BIND_HOST", "127.0.0.1")
LOCAL_BIND_PORT = int(os.getenv("LOCAL_BIND_PORT", "3308"))

MYSQL_USER = os.getenv("MYSQL_USER", "suraj")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "8A42TgtE[pA@")
MYSQL_DB_NAME = os.getenv("MYSQL_DB_NAME", "transbnk_prod_payout")

PAYOUT_TABLE_NAME = os.getenv("PAYOUT_TABLE_NAME", "payout")

# Loki Configuration (unchanged)
LOKI_BASE_URL = os.getenv("LOKI_BASE_URL", "https://loki-prod.trusthub.in")

# LLM Configuration (unchanged)
vertex_gemini_model_id = "gemini-2.0-flash-001"
litellm_vertex_model_name = f"vertex_ai/{vertex_gemini_model_id}"
llm = ChatLiteLLM(
    model=litellm_vertex_model_name,
    vertex_project=os.getenv("VERTEX_PROJECT_ID", "transbnk-ai"),
    vertex_location=os.getenv("VERTEX_REGION", "asia-south1"),
    temperature=0.2,
    request_timeout=120
)

# --- SSH TUNNEL FUNCTIONS ---

def start_ssh_tunnel_with_subprocess():
    """Start SSH tunnel using subprocess for better reliability"""
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        raise RuntimeError("ssh binary not found on PATH.")

    ssh_cmd = [
        ssh_path,
        "-i", SSH_PKEY_PATH,
        "-N", 
        "-L", f"{LOCAL_BIND_HOST}:{LOCAL_BIND_PORT}:{REMOTE_DB_HOST}:{REMOTE_DB_PORT}",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        f"{SSH_USER}@{SSH_HOST}",
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x08000000  # CREATE_NO_WINDOW on Windows

    proc = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags
    )

    # Wait briefly for tunnel to establish
    time.sleep(1.2)

    if proc.poll() is not None:
        stdout, stderr = proc.communicate(timeout=1)
        raise RuntimeError(f"SSH tunnel process exited: rc={proc.returncode}, stderr={stderr.decode()}")

    return proc

def stop_ssh_tunnel(proc: subprocess.Popen):
    """Stop SSH tunnel process gracefully"""
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT) 
            proc.terminate()
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception as e:
        logger.warning(f"Graceful tunnel shutdown failed: {e}")
        try:
            proc.kill()
            logger.info("Tunnel process killed forcefully")
        except Exception:
            pass

def connect_via_tunnel():
    """Establish database connection through SSH tunnel"""
    ssh_proc = None
    try:
        logger.info("🔌 Starting SSH tunnel...")
        ssh_proc = start_ssh_tunnel_with_subprocess()

        safe_password = quote_plus(MYSQL_PASSWORD)
        db_uri = f"mysql+mysqldb://{MYSQL_USER}:{safe_password}@{LOCAL_BIND_HOST}:{LOCAL_BIND_PORT}/{MYSQL_DB_NAME}"
        
        engine = create_engine(
            db_uri,
            pool_pre_ping=True,
            pool_recycle=28800,  # 8 hours to match server timeout
            pool_size=10,
            max_overflow=20,
            echo=False,
            connect_args={"connect_timeout": 15}
        )
        
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            logger.info("✅ SSH tunnel + DB connection working")
            
        return engine, ssh_proc

    except Exception as e:
        logger.error(f"❌ SSH tunnel connection failed: {e}")
        if ssh_proc:
            stop_ssh_tunnel(ssh_proc)
        raise

# --- MODIFIED DATABASE INITIALIZATION ---

db_engine = None
ssh_tunnel_proc = None
db_table_info_str = COMPACT_DB_SCHEMA

# Skip database connection if disabled
USE_DATABASE = os.getenv("USE_DATABASE", "true").lower() == "true"

if USE_DATABASE:
    logger.info(f"Attempting to connect to MySQL database '{MYSQL_DB_NAME}' via SSH tunnel...")
    
    # Check if SSH key exists
    if not os.path.exists(SSH_PKEY_PATH):
        logger.warning(f"SSH key file not found at {SSH_PKEY_PATH}")
        logger.warning("Continuing without database functionality...")
        db_engine = None
    else:
        try:
            db_engine, ssh_tunnel_proc = connect_via_tunnel()
            logger.info(f"✅ Successfully connected to database '{MYSQL_DB_NAME}' via SSH tunnel")
            
        except Exception as e:
            logger.warning(f"Database connection via SSH tunnel failed: {e}")
            logger.warning("Continuing without database functionality...")
            db_engine = None
            if ssh_tunnel_proc:
                stop_ssh_tunnel(ssh_tunnel_proc)
                ssh_tunnel_proc = None
else:
    logger.info("Database functionality disabled (USE_DATABASE=false)")
    db_engine = None

logger.info("Database schema loaded for LLM context")

# --- Add cleanup function for graceful shutdown ---
import atexit

def cleanup_resources():
    """Clean up SSH tunnel and database connections"""
    if ssh_tunnel_proc:
        logger.info("🔒 Closing SSH tunnel...")
        stop_ssh_tunnel(ssh_tunnel_proc)
    if db_engine:
        db_engine.dispose()
        logger.info("🔒 Database connection closed")

atexit.register(cleanup_resources)
# Configuration
LOKI_BASE_URL = os.getenv("LOKI_BASE_URL", "https://loki-prod.trusthub.in")

# LLM Configuration
vertex_gemini_model_id = "gemini-2.0-flash-001"
litellm_vertex_model_name = f"vertex_ai/{vertex_gemini_model_id}"
llm = ChatLiteLLM(
    model=litellm_vertex_model_name,
    vertex_project=os.getenv("VERTEX_PROJECT_ID", "transbnk-ai"),
    vertex_location=os.getenv("VERTEX_REGION", "asia-south1"),
    temperature=0.2,
    request_timeout=120
)

COMPACT_DB_SCHEMA = f"""
* Table `{PAYOUT_TABLE_NAME}` (aliased as `a`): Stores account details for each entity. 
Key columns: `account_id` (account_id is primary key)- Type: UUID (string in format xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx), 
`entity_id`- Type: UUID (string in format xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx) ,
`account_number` (account_number is encrypted), 
`program_id`- Type: Integer (in format 21, 04, 46), 
`available_balance`- Type: Integer (in format 91993.88, 651458777.93), 
`bank_code`- Type: String (in format AUBL, RATN)."""

# Define the state schema
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    investigation_id: str
    user_query: str
    customer_id: Optional[str]
    transaction_id: Optional[str]
    timestamp: Optional[str]
    
    # Agent outputs
    extracted_params: Optional[dict]
    loki_results: Optional[dict]
    mysql_query: Optional[str]
    mysql_results: Optional[dict]
    final_summary: Optional[dict]
    
    # Tracking
    current_agent: str
    agent_logs: List[str]
    errors: List[str]

# -------------------- LOKI SERVICE DISCOVERY --------------------

def discover_loki_services() -> List[str]:
    """Discover available services from Loki"""
    try:
        response = requests.get(
            f"{LOKI_BASE_URL}/loki/api/v1/label/app/values",
            timeout=10,
            verify=False
        )
        if response.status_code == 200:
            data = response.json()
            services = data.get("data", [])
            return services
        return []
    except Exception as e:
        logging.error(f"Failed to discover services: {e}")
        return []

def get_available_labels() -> List[str]:
    """Get all available label names from Loki"""
    try:
        response = requests.get(
            f"{LOKI_BASE_URL}/loki/api/v1/labels",
            timeout=10,
            verify=False
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", [])
        return []
    except Exception as e:
        logging.error(f"Failed to get labels: {e}")
        return []
    
# -------------------- AGENT 1: PARAMETER EXTRACTOR --------------------

def parameter_extractor_agent(state: AgentState) -> AgentState:
    """Agent 1: Extract parameters from natural language query"""
    logs = []
    logs.append("🕵️ Agent 1: Data extraction started")
    
    try:
        # Get available services for context
        available_services = payout-prod-prod
        services_context = f"Available services: {', '.join(available_services[:10])}" if available_services else ""
        
        system_prompt = f"""
        You are a parameter extraction agent for technical support. 
        Extract key information from user queries about payment failures and details of the transactions.
        Extract: transaction id, any transaction reference number, customer id, timestamp, 
        and identify the main issue.
        
        {services_context}
        
        Database Schema Context:
        {db_table_info_str}
        """
        
        human_prompt = f"""
        User Query: {state['user_query']}
        
        Extract the following information as JSON:
        - transaction_id (look for patterns like payout-0648, txn-123. If not found, set to null)
        - customer_id (look for cust_, customer, user identifiers. If not found, set to null)
        - literal_search_phrase (Extract any specific error message, status code, or unique text the user mentioned. 
             Example: if user says "getting upstream timeout", extract "upstream timeout". 
             If user says "error 500", extract "error 500". Do NOT include generic words like "check" or "why".)
        - timestamp (extract time references. If not found, set to null)
        
        Note: The application is always "payout-prod-prod".
        """
        
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ])
        
        # Parse response
        content = response.content.strip()
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        
        extracted_params = json.loads(content)
        
        # Validate and normalize application_name
        app_name = extracted_params.get("application_name") or "payout-prod-prod"
        
        # Ensure app_name is a string
        if not isinstance(app_name, str) or not app_name:
            app_name = "payout-prod-prod"
            extracted_params["application_name"] = app_name
        
        if available_services and app_name not in available_services:
            # Try to find a matching service
            app_name_lower = app_name.lower()
            matched = None
            for service in available_services:
                if service:  # Ensure service is not None
                    if app_name_lower in service.lower() or service.lower() in app_name_lower:
                        matched = service
                        break
            if matched:
                logs.append(f"📝 Mapped '{app_name}' to available service '{matched}'")
                extracted_params["application_name"] = matched
            else:
                logs.append(f"⚠️ Service '{app_name}' not found, using default 'payout-prod-prod'")
                extracted_params["application_name"] = "payout-prod-prod"
        
        logs.append(f"📋 Extracted parameters: {json.dumps(extracted_params, indent=2)}")
        
        # Update state
        return {
            "extracted_params": extracted_params,
            "agent_logs": state["agent_logs"] + logs,
            "current_agent": "parameter_extractor",
            "messages": state["messages"] + [HumanMessage(content=f"Parameters extracted: {extracted_params}")]
        }
        
    except Exception as e:
        logs.append(f"❌ Parameter extraction failed: {str(e)}")
        return {
            "extracted_params": {
                "transaction_id": None,
                "customer_id": None,
                "entity_id": None,
                "account_id": None,
                "issue_type": "payment_failure",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "application_name": "payout-prod-prod",
                "search_keywords": ["failure", "error", "failed"]
            },
            "agent_logs": state["agent_logs"] + logs,
            "errors": state["errors"] + [f"Parameter extraction: {str(e)}"],
            "current_agent": "parameter_extractor"
        }

# -------------------- AGENT 2: LOKI INVESTIGATOR --------------------

def loki_investigator_agent(state: AgentState) -> AgentState:
    """Agent 2: Investigate logs using Loki"""
    logs = []
    logs.append("🔍 Agent 2: Loki investigation started")
    
    try:
        params = state.get("extracted_params", {})
        transaction_id = state.get("transaction_id") or params.get("transaction_id")
        customer_id = params.get("customer_id")
        entity_id = params.get("entity_id")
        account_id = params.get("account_id")
        app_name = params.get("application_name", "payout-prod-prod")
        
        # Clean up transaction_id - remove N/A, null, None, etc.
        if transaction_id in ["N/A", "null", "None", "unknown", ""]:
            transaction_id = None
        
        if not transaction_id:
            logs.append("⚠️ No transaction ID available, fetching recent logs from service")
        
        # Determine time range
        if state.get("timestamp"):
            try:
                timestamp_str = state["timestamp"]
                # Clean up timestamp - remove N/A, null, etc.
                if timestamp_str in ["N/A", "null", "None", "unknown", ""]:
                    incident_time = datetime.now(timezone.utc)
                else:
                    incident_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except:
                incident_time = datetime.now(timezone.utc)
        else:
            incident_time = datetime.now(timezone.utc)
        
        # Use wider window - 30 minutes before, 5 minutes after
        start_time = incident_time - timedelta(minutes=30)
        end_time = incident_time + timedelta(minutes=5)
        
        logs.append(f"⏰ Searching logs from {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')} (35-minute window)")
        
        # Query Loki with primary service
        if transaction_id:
            logs.append(f"🎯 Transaction search: service='{app_name}', transaction='{transaction_id}'")
            loki_results = query_loki_transaction(app_name, transaction_id, start_time, end_time)
        else:
            logs.append(f"🔍 General search: service='{app_name}'")
            loki_results = query_loki_general(app_name, start_time, end_time, customer_id)
        
        logs.append(f"📊 Loki query returned {loki_results.get('logs_found', 0)} logs")
        
        # Analyze logs with LLM to understand what database queries are needed
        if loki_results.get('logs_found', 0) > 0:
            logs.append("🤖 Analyzing logs to determine database queries needed...")
            log_analysis = analyze_loki_logs_for_db_query(loki_results.get("sample_logs", []), 
                                                         transaction_id or "all logs",
                                                         customer_id, entity_id, account_id)
            loki_results["analysis"] = log_analysis
        else:
            logs.append("❌ No logs found across all services")
            loki_results["analysis"] = {
                "error": "No logs found in time range",
                "suggestion": "Service may not be logging or logs are in a different location",
                "db_queries_needed": ["general_account_check"]
            }
        
        logs.append("✅ Loki investigation completed")
        
        return {
            "loki_results": loki_results,
            "agent_logs": state["agent_logs"] + logs,
            "current_agent": "loki_investigator",
            "messages": state["messages"] + [HumanMessage(content=f"Loki investigation completed: {loki_results.get('logs_found', 0)} logs found")]
        }
        
    except Exception as e:
        logs.append(f"❌ Loki investigation failed: {str(e)}")
        return {
            "loki_results": {"error": str(e), "logs_found": 0},
            "agent_logs": state["agent_logs"] + logs,
            "errors": state["errors"] + [f"Loki investigation: {str(e)}"],
            "current_agent": "loki_investigator"
        }

def query_loki_transaction(app_name: str, transaction_id: str, start_time: datetime, end_time: datetime) -> dict:
    """Query Loki for specific transaction using 'app' label"""
    try:
        # Use 'app' label as confirmed by user
        logql = f'{{app="{app_name}"}} |~ "{transaction_id}"'
        start_ns = int(start_time.timestamp() * 1e9)
        end_ns = int(end_time.timestamp() * 1e9)
        
        response = requests.get(
            f"{LOKI_BASE_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": start_ns,
                "end": end_ns,
                "limit": 1000
            },
            timeout=30,
            verify=False
        )
        
        if response.status_code != 200:
            return {
                "logs_found": 0,
                "sample_logs": [],
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "query_used": logql
            }
        
        result = response.json()["data"]["result"]
        
        logs = []
        for stream in result:
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                logs.append({
                    "@timestamp": datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc).isoformat(),
                    "message": line,
                    "labels": labels
                })
        
        return {
            "logs_found": len(logs),
            "sample_logs": logs[:20],  # Increased sample size
            "total_logs": len(logs),
            "query_used": logql,
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            }
        }
        
    except Exception as e:
        return {
            "logs_found": 0,
            "sample_logs": [],
            "error": str(e),
            "query_used": f'{{app="{app_name}"}} |~ "{transaction_id}"'
        }

def query_loki_general(app_name: str, start_time: datetime, end_time: datetime, customer_id: str = None) -> dict:
    """Query Loki for general logs when no transaction ID is available"""
    try:
        logql = f'{{app="{app_name}"}}'
        if customer_id:
            logql += f' |~ "{customer_id}"'
        
        start_ns = int(start_time.timestamp() * 1e9)
        end_ns = int(end_time.timestamp() * 1e9)
        
        response = requests.get(
            f"{LOKI_BASE_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": start_ns,
                "end": end_ns,
                "limit": 500
            },
            timeout=30,
            verify=False
        )
        
        if response.status_code != 200:
            return {
                "logs_found": 0,
                "sample_logs": [],
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "query_used": logql
            }
        
        result = response.json()["data"]["result"]
        
        logs = []
        for stream in result:
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                logs.append({
                    "@timestamp": datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc).isoformat(),
                    "message": line,
                    "labels": labels
                })
        
        return {
            "logs_found": len(logs),
            "sample_logs": logs[:15],
            "total_logs": len(logs),
            "query_used": logql,
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            }
        }
        
    except Exception as e:
        return {
            "logs_found": 0,
            "sample_logs": [],
            "error": str(e),
            "query_used": logql
        }

def analyze_loki_logs_for_db_query(logs: List[dict], transaction_id: str, customer_id: str = None, 
                                 entity_id: str = None, account_id: str = None) -> dict:
    """Analyze Loki logs to determine what database queries are needed"""
    system_prompt = f"""
    You are a database query planning agent. Analyze application logs to identify what database queries should be run 
    to investigate the issue further. Based on the logs, suggest specific SQL queries to extract relevant data.
    
    Database Schema:
    {db_table_info_str}
    
    Focus on identifying:
    1. Account information queries
    2. Transaction history queries  
    3. Balance checks
    4. Entity/customer information
    5. Related transactions
    
    Return JSON with the actual SQL queries that should be executed.
    """
    
    sample_logs = json.dumps(logs[:10], indent=2) if logs else "No logs found"
    
    human_prompt = f"""
    Analyze these logs and determine what database queries are needed:
    
    Transaction ID: {transaction_id or 'Not available'}
    Customer ID: {customer_id or 'Not available'}
    Entity ID: {entity_id or 'Not available'}
    Account ID: {account_id or 'Not available'}
    
    Logs:
    {sample_logs}
    
    Provide JSON with:
    - db_queries_needed (list of query types: account_check, transaction_history, balance_check, entity_info, related_transactions)
    - suggested_queries (list of actual SQL queries that should be run)
    - reasoning (why each query is needed based on log evidence)
    """
    
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ])
        
        content = response.content.strip()
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        
        return json.loads(content)
    except Exception as e:
        return {
            "db_queries_needed": ["general_account_check"],
            "suggested_queries": [
                f"SELECT * FROM {PAYOUT_TABLE_NAME} WHERE account_id IS NOT NULL LIMIT 10"
            ],
            "reasoning": f"Default query due to analysis error: {str(e)}",
            "error": str(e)
        }

# -------------------- AGENT 3: MYSQL QUERY EXECUTOR --------------------

def mysql_query_agent(state: AgentState) -> AgentState:
    """Agent 3: Execute MySQL queries based on Loki analysis"""
    logs = []
    logs.append("🗄️ Agent 3: MySQL query execution started")
    
    # Check if database is available
    if not db_engine:
        logs.append("⚠️ Database connection not available - skipping MySQL queries")
        return {
            "mysql_query": "[]",
            "mysql_results": {
                "queries_executed": 0,
                "successful_queries": 0,
                "query_results": [],
                "summary": "Database not available"
            },
            "agent_logs": state["agent_logs"] + logs,
            "current_agent": "mysql_query_agent",
            "messages": state["messages"] + [HumanMessage(content="MySQL queries skipped - database not available")]
        }
    
    try:
        params = state.get("extracted_params", {})
        loki_analysis = state.get("loki_results", {}).get("analysis", {})
        
        # Get suggested queries from Loki analysis
        suggested_queries = loki_analysis.get("suggested_queries", [])
        
        if not suggested_queries:
            logs.append("⚠️ No specific queries suggested, creating default queries")
            # Create basic queries based on available parameters
            suggested_queries = create_default_queries(params)
        
        logs.append(f"📋 Executing {len(suggested_queries)} database queries")
        
        # Execute queries and collect results
        query_results = []
        successful_queries = 0
        
        for i, query in enumerate(suggested_queries):
            try:
                logs.append(f"🔍 Executing query {i+1}: {query[:100]}...")
                result = execute_mysql_query(query)
                query_results.append({
                    "query": query,
                    "result": result,
                    "status": "success",
                    "rows_returned": len(result.get("data", [])) if result.get("data") else 0
                })
                successful_queries += 1
                logs.append(f"✅ Query {i+1} returned {len(result.get('data', []))} rows")
            except Exception as e:
                query_results.append({
                    "query": query,
                    "error": str(e),
                    "status": "failed"
                })
                logs.append(f"❌ Query {i+1} failed: {str(e)}")
        
        mysql_results = {
            "queries_executed": len(suggested_queries),
            "successful_queries": successful_queries,
            "query_results": query_results,
            "summary": f"Executed {len(suggested_queries)} queries, {successful_queries} successful"
        }
        
        logs.append(f"✅ Database queries completed: {successful_queries}/{len(suggested_queries)} successful")
        
        return {
            "mysql_query": json.dumps(suggested_queries),
            "mysql_results": mysql_results,
            "agent_logs": state["agent_logs"] + logs,
            "current_agent": "mysql_query_agent",
            "messages": state["messages"] + [HumanMessage(content=f"MySQL queries executed: {successful_queries} successful")]
        }
        
    except Exception as e:
        logs.append(f"❌ MySQL query execution failed: {str(e)}")
        return {
            "mysql_query": "",
            "mysql_results": {"error": str(e), "queries_executed": 0, "successful_queries": 0},
            "agent_logs": state["agent_logs"] + logs,
            "errors": state["errors"] + [f"MySQL query execution: {str(e)}"],
            "current_agent": "mysql_query_agent"
        }

def create_default_queries(params: dict) -> List[str]:
    """Create default MySQL queries based on available parameters"""
    queries = []
    
    transaction_id = params.get("transaction_id")
    customer_id = params.get("customer_id")
    entity_id = params.get("entity_id")
    account_id = params.get("account_id")
    
    # Basic account information query
    if account_id:
        queries.append(f"SELECT * FROM {PAYOUT_TABLE_NAME} WHERE account_id = '{account_id}'")
    elif entity_id:
        queries.append(f"SELECT * FROM {PAYOUT_TABLE_NAME} WHERE entity_id = '{entity_id}'")
    elif customer_id:
        # Try to find accounts by customer reference
        queries.append(f"SELECT * FROM {PAYOUT_TABLE_NAME} WHERE entity_id LIKE '%{customer_id}%' OR account_id LIKE '%{customer_id}%'")
    else:
        # General sample query
        queries.append(f"SELECT account_id, entity_id, available_balance, bank_code FROM {PAYOUT_TABLE_NAME} LIMIT 5")
    
    # Balance check query
    queries.append(f"SELECT AVG(available_balance) as avg_balance, COUNT(*) as total_accounts FROM {PAYOUT_TABLE_NAME}")
    
    return queries

def execute_mysql_query(query: str) -> dict:
    """Execute a MySQL query and return results"""
    try:
        with db_engine.connect() as connection:
            # Use text() to ensure proper SQL parsing
            result = connection.execute(text(query))
            
            # Convert to list of dicts
            columns = result.keys()
            data = [dict(zip(columns, row)) for row in result]
            
            return {
                "data": data,
                "row_count": len(data),
                "columns": list(columns)
            }
    except Exception as e:
        logger.error(f"MySQL query execution error: {e}")
        return {
            "error": str(e),
            "data": [],
            "row_count": 0
        }

# -------------------- AGENT 4: FINAL ANALYZER --------------------

def final_analyzer_agent(state: AgentState) -> AgentState:
    """Agent 4: Combine Loki logs and MySQL results for final analysis"""
    logs = []
    logs.append("🔬 Agent 4: Final analysis started")
    
    try:
        # Gather all data from previous agents
        params = state.get("extracted_params", {})
        loki_results = state.get("loki_results", {})
        mysql_results = state.get("mysql_results", {})
        
        # Create comprehensive summary combining both data sources
        final_summary = create_combined_analysis(params, loki_results, mysql_results, state.get("user_query", ""))
        logs.append("✅ Final analysis completed")
        
        logs.append("🎯 Investigation completed successfully!")
        
        return {
            "final_summary": final_summary,
            "agent_logs": state["agent_logs"] + logs,
            "current_agent": "final_analyzer",
            "messages": state["messages"] + [HumanMessage(content=f"Final analysis: {final_summary.get('executive_summary', '')}")]
        }
        
    except Exception as e:
        logs.append(f"❌ Final analysis failed: {str(e)}")
        return {
            "final_summary": {"error": str(e)},
            "agent_logs": state["agent_logs"] + logs,
            "errors": state["errors"] + [f"Final analysis: {str(e)}"],
            "current_agent": "final_analyzer"
        }

def create_combined_analysis(params: dict, loki_results: dict, mysql_results: dict, user_query: str) -> dict:
    """Create final analysis combining Loki logs and MySQL data"""
    system_prompt = f"""
    You are a senior support engineer analyzing both application logs and database data.
    Combine insights from Loki logs and MySQL query results to provide comprehensive analysis.
    
    Database Schema Context:
    {db_table_info_str}
    
    Provide clear, actionable recommendations based on BOTH data sources.
    """
    
    # Prepare data for analysis
    loki_analysis = loki_results.get('analysis', {})
    mysql_data = []
    
    for query_result in mysql_results.get('query_results', []):
        if query_result.get('status') == 'success':
            mysql_data.append({
                "query": query_result.get('query', ''),
                "data_sample": query_result.get('result', {}).get('data', [])[:3]  # First 3 rows
            })
    
    human_prompt = f"""
    Complete Investigation Data:
    
    User Query: {user_query}
    
    Extracted Parameters: {json.dumps(params, indent=2)}
    
    Loki Log Analysis:
    - Logs Found: {loki_results.get('logs_found', 0)}
    - Analysis: {json.dumps(loki_analysis, indent=2)}
    
    MySQL Database Results:
    - Queries Executed: {mysql_results.get('queries_executed', 0)}
    - Successful Queries: {mysql_results.get('successful_queries', 0)}
    - Query Results: {json.dumps(mysql_data, indent=2)}
    
    Create final comprehensive analysis as JSON:
    - executive_summary (brief overview combining log and database insights)
    - technical_root_cause (detailed explanation based on both data sources)
    - transaction_status (final determined status)
    - data_consistency_check (do logs and database data tell the same story?)
    - account_health_status (based on database balances and log patterns)
    - confidence_score (0.0-1.0 based on data completeness and consistency)
    - recommended_actions (specific steps to resolve)
    - immediate_next_steps (what support should do now)
    - risk_assessment (low/medium/high risk based on findings)
    
    Focus on connecting log events with database state to tell a complete story.
    """
    
    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ])
        
        content = response.content.strip()
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        
        summary = json.loads(content)
        
        # Add metadata
        summary["investigation_metadata"] = {
            "logs_analyzed": loki_results.get('logs_found', 0),
            "db_queries_executed": mysql_results.get('queries_executed', 0),
            "service_searched": params.get('application_name', 'unknown'),
            "transaction_id": params.get('transaction_id', 'unknown'),
            "data_sources_used": ["loki_logs", "mysql_database" if db_engine else "loki_logs_only"]
        }
        
        return summary
        
    except Exception as e:
        return {
            "executive_summary": f"Investigation completed but analysis generation failed: {str(e)}",
            "technical_root_cause": "Unable to generate combined analysis",
            "transaction_status": loki_analysis.get('transaction_status', 'unknown'),
            "data_consistency_check": "Unknown - analysis failed",
            "confidence_score": 0.1,
            "recommended_actions": ["Manual review of both logs and database required"],
            "immediate_next_steps": ["Contact engineering team for manual investigation"],
            "risk_assessment": "Unknown",
            "investigation_metadata": {
                "error": str(e),
                "data_sources_used": ["loki_logs", "mysql_database" if db_engine else "loki_logs_only"]
            }
        }

# -------------------- LANGGRAPH ORCHESTRATOR --------------------

def should_continue(state: AgentState) -> str:
    """Determine next step in workflow"""
    current_agent = state.get("current_agent", "")
    
    agent_flow = {
        "parameter_extractor": "loki_investigator",
        "loki_investigator": "mysql_query_agent",  # Now goes to MySQL agent
        "mysql_query_agent": "final_analyzer",     # Then to final analyzer
        "final_analyzer": END
    }
    
    next_agent = agent_flow.get(current_agent, END)
    
    # Check for critical errors (only stop if we have multiple critical failures)
    if state.get("errors") and len(state["errors"]) > 3:
        return END
    
    return next_agent

# Build the graph
workflow = StateGraph(AgentState)

# Add nodes (now 4 agents)
workflow.add_node("parameter_extractor", parameter_extractor_agent)
workflow.add_node("loki_investigator", loki_investigator_agent)
workflow.add_node("mysql_query_agent", mysql_query_agent)  # New agent
workflow.add_node("final_analyzer", final_analyzer_agent)  # Renamed from summarizer

# Define edges
workflow.set_entry_point("parameter_extractor")
workflow.add_conditional_edges(
    "parameter_extractor",
    should_continue,
    {
        "loki_investigator": "loki_investigator",
        END: END
    }
)
workflow.add_conditional_edges(
    "loki_investigator",
    should_continue,
    {
        "mysql_query_agent": "mysql_query_agent",  # New path
        END: END
    }
)
workflow.add_conditional_edges(
    "mysql_query_agent",
    should_continue,
    {
        "final_analyzer": "final_analyzer",  # Goes to final analyzer
        END: END
    }
)
workflow.add_conditional_edges(
    "final_analyzer",
    should_continue,
    {
        END: END
    }
)

# Compile the graph
graph = workflow.compile()

# -------------------- FASTAPI WRAPPER --------------------

app = FastAPI(title="Loki Log Analyzer", version="1.0.0")

class InvestigationRequest(BaseModel):
    query: str = Field(..., description="User's natural language query")
    customer_id: Optional[str] = None
    transaction_id: Optional[str] = None
    timestamp: Optional[str] = None

class InvestigationResponse(BaseModel):
    investigation_id: str
    status: str
    extracted_params: Optional[dict] = None
    raw_logs: Optional[List[dict]] = None
    logs_found: int = 0
    mysql_queries: Optional[List[str]] = None
    mysql_results: Optional[dict] = None
    final_summary: Optional[dict] = None
    agent_logs: List[str]
    errors: List[str]
    processing_time_seconds: float

@app.get("/check-service-logs")
async def check_service_logs(service: str = "payout-prod-prod", minutes: int = 60):
    """Enhanced version with better response structure"""
    try:
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(minutes=minutes)
        
        logql = f'{{app="{service}"}}'
        start_ns = int(start_time.timestamp() * 1e9)
        end_ns = int(now.timestamp() * 1e9)
        
        response = requests.get(
            f"{LOKI_BASE_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": start_ns,
                "end": end_ns,
                "limit": 10
            },
            timeout=30,
            verify=False
        )
        
        if response.status_code != 200:
            return {
                "status": "error",
                "service": service,
                "error": f"HTTP {response.status_code}",
                "logs_found": 0,
                "has_logs": False,
                "query_used": logql,
                "time_range_minutes": minutes
            }
        
        result = response.json().get("data", {}).get("result", [])
        total_logs = sum(len(stream.get("values", [])) for stream in result)
        
        sample_logs = []
        for stream in result[:3]:
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", [])[:2]:
                sample_logs.append({
                    "@timestamp": datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc).isoformat(),
                    "message": line[:500],
                    "labels": labels
                })
        
        # Get available services for better UX
        available_services = []
        try:
            svc_response = requests.get(
                f"{LOKI_BASE_URL}/loki/api/v1/label/app/values",
                timeout=10,
                verify=False
            )
            if svc_response.status_code == 200:
                available_services = svc_response.json().get("data", [])
        except:
            pass
        
        return {
            "status": "success",
            "service": service,
            "logs_found": total_logs,
            "has_logs": total_logs > 0,
            "time_range_minutes": minutes,
            "query_used": logql,
            "sample_logs": sample_logs,
            "available_services": available_services[:10],
            "message": f"Found {total_logs} logs" if total_logs > 0 else "No logs found"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "service": service,
            "error": str(e),
            "logs_found": 0,
            "has_logs": False
        }

@app.post("/investigate", response_model=InvestigationResponse)
async def investigate(request: InvestigationRequest):
    """Execute complete investigation workflow"""
    start_time = datetime.now(timezone.utc)
    
    try:
        # Create initial state
        initial_state = AgentState(
            messages=[],
            investigation_id=str(uuid.uuid4()),
            user_query=request.query,
            customer_id=request.customer_id,
            transaction_id=request.transaction_id,
            timestamp=request.timestamp,
            extracted_params=None,
            loki_results=None,
            mysql_query=None,
            mysql_results=None,
            final_summary=None,
            current_agent="",
            agent_logs=[],
            errors=[]
        )
        
        # Execute the graph
        result = graph.invoke(initial_state)
        
        # Calculate processing time
        processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        # Extract data for response
        loki_results = result.get("loki_results", {})
        raw_logs = loki_results.get("sample_logs", [])
        logs_found = loki_results.get("logs_found", 0)
        
        mysql_results = result.get("mysql_results", {})
        mysql_queries = []
        for query_result in mysql_results.get("query_results", []):
            mysql_queries.append(query_result.get("query", ""))
        
        return InvestigationResponse(
            investigation_id=result["investigation_id"],
            status="completed",
            extracted_params=result.get("extracted_params"),
            raw_logs=raw_logs,
            logs_found=logs_found,
            mysql_queries=mysql_queries,
            mysql_results=mysql_results,
            final_summary=result.get("final_summary"),
            agent_logs=result.get("agent_logs", []),
            errors=result.get("errors", []),
            processing_time_seconds=processing_time
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Investigation failed: {str(e)}")

@app.get("/health")
async def health():
    """Health check endpoint with SSH tunnel status"""
    loki_healthy = False
    db_healthy = False
    tunnel_healthy = False
    
    try:
        # Test Loki connectivity
        response = requests.get(
            f"{LOKI_BASE_URL}/query", 
            timeout=5, 
            verify=False
        )
        loki_healthy = response.status_code == 200
    except Exception as e:
        loki_healthy = False
        logger.debug(f"Loki health check failed: {e}")
    
    # Check SSH tunnel and database
    if ssh_tunnel_proc and ssh_tunnel_proc.poll() is None:
        tunnel_healthy = True
        if db_engine:
            try:
                with db_engine.connect() as conn:
                    result = conn.execute(text("SELECT 1")).scalar()
                    db_healthy = result == 1
            except Exception as e:
                db_healthy = False
                logger.debug(f"Database health check failed: {e}")
    else:
        tunnel_healthy = False
        db_healthy = False
    
    # Determine overall status
    status = "healthy"
    if not loki_healthy:
        status = "degraded"
    elif not tunnel_healthy and USE_DATABASE:
        status = "degraded"
    
    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "loki_connection": "connected" if loki_healthy else "disconnected",
        "ssh_tunnel": "active" if tunnel_healthy else "inactive" if USE_DATABASE else "disabled",
        "database_connection": "connected" if db_healthy else "disconnected" if db_engine else "disabled",
        "database_available": db_engine is not None,
        "loki_url": LOKI_BASE_URL,
        "details": {
            "tunnel_process": "running" if tunnel_healthy else "stopped",
            "database_via_tunnel": "yes" if db_healthy else "no"
        }
    }

if __name__ == "__main__":
    print(f""" 🚀 Starting FastAPI server... 📍 Loki URL: {LOKI_BASE_URL}""")
    print(f""" 🔐 SSH Tunnel: {'ENABLED' if USE_DATABASE else 'DISABLED'}""")
    print(f""" 💾 Database via Tunnel: {'CONNECTED' if db_engine else 'DISCONNECTED'}""")
    
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Server error: {e}")
    finally:
        cleanup_resources()