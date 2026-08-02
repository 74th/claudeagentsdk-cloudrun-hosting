"""Minimal ADK agent for testing Agent Platform long-running query jobs."""

from google.adk.agents import Agent


root_agent = Agent(
    name="async_stream_poc",
    model="gemini-2.5-flash",
    instruction="Reply with a concise acknowledgement of the user's message.",
)
