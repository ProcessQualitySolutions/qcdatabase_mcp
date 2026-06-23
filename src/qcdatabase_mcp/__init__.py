"""Local MCP server for QCDatabase.AI.

A small, production-ready Model Context Protocol server that lets an AI assistant
do everyday construction quality-control work in QC Database: set the project you
are working in, upload records, read extracted data, and - most importantly -
find and close out the reference requests that stand between a project and a
complete turnover package.

See https://qcdatabase.ai/mcp_server_spec.md for the full specification.
"""

__version__ = "0.1.0"

BASE_URL = "https://qcdatabase.ai"
