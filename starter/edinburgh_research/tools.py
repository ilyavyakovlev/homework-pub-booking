"""Ex5 tools. Four tools the agent uses to research an Edinburgh booking.

Each tool:
  1. Reads its fixture from sample_data/ (DO NOT modify the fixtures).
  2. Logs its arguments and output into _TOOL_CALL_LOG (see integrity.py).
  3. Returns a ToolResult with success=True/False, output=dict, summary=str.

The grader checks for:
  * Correct parallel_safe flags (reads True, generate_flyer False).
  * Every tool's results appear in _TOOL_CALL_LOG.
  * Tools fail gracefully on missing fixtures or bad inputs (ToolError,
    not RuntimeError).
"""

from __future__ import annotations

import json
from pathlib import Path

from sovereign_agent.errors import ToolError
from sovereign_agent.session.directory import Session
from sovereign_agent.tools.registry import ToolRegistry, ToolResult, _RegisteredTool

from .integrity import _TOOL_CALL_LOG, record_tool_call

_SAMPLE_DATA = Path(__file__).parent / "sample_data"


# ---------------------------------------------------------------------------
# Helper — load a JSON fixture, raising ToolError if the file is absent
# ---------------------------------------------------------------------------
def _load_fixture(filename: str) -> object:
    """Read and parse a JSON fixture from sample_data/.

    Raises ToolError(SA_TOOL_DEPENDENCY_MISSING) rather than letting
    FileNotFoundError propagate — the agent can recover from a structured
    error but not from an unexpected exception.
    """
    path = _SAMPLE_DATA / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ToolError(
            "SA_TOOL_DEPENDENCY_MISSING",
            f"Required fixture '{filename}' not found at {path}",
        )


# ---------------------------------------------------------------------------
# TODO 1 — venue_search
# ---------------------------------------------------------------------------
def venue_search(near: str, party_size: int, budget_max_gbp: int = 1000) -> ToolResult:
    """Search for Edinburgh venues near <near> that can seat the party.

    Reads sample_data/venues.json. Filters by:
      * open_now == True
      * area contains <near> (case-insensitive substring match)
      * seats_available_evening >= party_size
      * hire_fee_gbp + min_spend_gbp <= budget_max_gbp

    Returns a ToolResult with:
      output: {"near": ..., "party_size": ..., "results": [<venue dicts>], "count": int}
      summary: "venue_search(<near>, party=<N>): <count> result(s)"

    MUST call record_tool_call(...) before returning so the integrity
    check can see what data was produced.
    """
    # Load the full venue list; raises ToolError if the file is missing.
    venues: list[dict] = _load_fixture("venues.json")  # type: ignore[assignment]

    results = []
    for venue in venues:
        # Only venues currently open for business.
        if not venue.get("open_now"):
            continue

        # Area must contain the search term as a case-insensitive substring.
        if near.lower() not in venue.get("area", "").lower():
            continue

        # The venue must have enough evening seats for the whole party.
        if venue.get("seats_available_evening", 0) < party_size:
            continue

        # The fixed costs (hire fee + minimum spend) must fit the budget.
        fixed_cost = venue.get("hire_fee_gbp", 0) + venue.get("min_spend_gbp", 0)
        if fixed_cost > budget_max_gbp:
            continue

        results.append(venue)

    output = {
        "near": near,
        "party_size": party_size,
        "budget_max_gbp": budget_max_gbp,
        "results": results,
        "count": len(results),
    }

    # Log before returning so the integrity check can trace every call.
    record_tool_call(
        "venue_search",
        {"near": near, "party_size": party_size, "budget_max_gbp": budget_max_gbp},
        output,
    )

    # Spiral detection — Qwen3-32B instruct tends to retry venue_search
    # with escalating params when it gets 0 results, never reaching the
    # other tools. After 2 successful calls the LLM has enough data; any
    # further call is a loop. Count AFTER logging so this call is included.
    search_count = sum(1 for r in _TOOL_CALL_LOG if r.tool_name == "venue_search")
    if search_count >= 3:
        return ToolResult(
            success=False,
            output={"error": "too_many_searches", "call_count": search_count},
            summary=(
                "STOP calling venue_search. "
                f"You have already searched {search_count} times. "
                "Use the results you already have and proceed to "
                "get_weather, calculate_cost, and generate_flyer."
            ),
        )

    return ToolResult(
        success=True,
        output=output,
        summary=f"venue_search({near!r}, party={party_size}): {len(results)} result(s)",
    )


# ---------------------------------------------------------------------------
# TODO 2 — get_weather
# ---------------------------------------------------------------------------
def get_weather(city: str, date: str) -> ToolResult:
    """Look up the scripted weather for <city> on <date> (YYYY-MM-DD).

    Reads sample_data/weather.json. Returns:
      output: {"city": str, "date": str, "condition": str, "temperature_c": int, ...}
      summary: "get_weather(<city>, <date>): <condition>, <temp>C"

    If the city or date is not in the fixture, return success=False with
    a clear ToolError (SA_TOOL_INVALID_INPUT). Do NOT raise.

    MUST call record_tool_call(...) before returning.
    """
    # Load the fixture; raises ToolError(SA_TOOL_DEPENDENCY_MISSING) if absent.
    weather_data: dict = _load_fixture("weather.json")  # type: ignore[assignment]

    # The fixture keys cities in lowercase.
    city_key = city.lower()
    city_data = weather_data.get(city_key)

    if city_data is None:
        # Return a failure result rather than raising — the caller can decide
        # whether to retry with a different city or surface the error to the user.
        err = ToolError(
            "SA_TOOL_INVALID_INPUT",
            f"City '{city}' not found in weather fixture. "
            f"Available: {list(weather_data.keys())}",
        )
        record_tool_call("get_weather", {"city": city, "date": date}, {})
        return ToolResult(success=False, output={}, summary=f"get_weather({city!r}, {date!r}): city not found", error=err)

    day_data = city_data.get(date)

    if day_data is None:
        # Date exists in the fixture for other cities but not for this one,
        # or the date is simply outside the scripted range.
        err = ToolError(
            "SA_TOOL_INVALID_INPUT",
            f"Date '{date}' not found for city '{city}'. "
            f"Available dates: {list(city_data.keys())}",
        )
        record_tool_call("get_weather", {"city": city, "date": date}, {})
        return ToolResult(success=False, output={}, summary=f"get_weather({city!r}, {date!r}): date not found", error=err)

    # Augment the raw fixture record with the lookup keys so callers
    # don't have to remember what they asked for.
    output = {"city": city, "date": date, **day_data}

    record_tool_call("get_weather", {"city": city, "date": date}, output)

    condition = output.get("condition", "unknown")
    temp = output.get("temperature_c", "?")
    return ToolResult(
        success=True,
        output=output,
        summary=f"get_weather({city!r}, {date!r}): {condition}, {temp}C",
    )


# ---------------------------------------------------------------------------
# TODO 3 — calculate_cost
# ---------------------------------------------------------------------------
def calculate_cost(
    venue_id: str,
    party_size: int,
    duration_hours: int,
    catering_tier: str = "bar_snacks",
) -> ToolResult:
    """Compute the total cost for a booking.

    Formula:
      base_per_head = base_rates_gbp_per_head[catering_tier]
      venue_mult    = venue_modifiers[venue_id]
      subtotal      = base_per_head * venue_mult * party_size * max(1, duration_hours)
      service       = subtotal * service_charge_percent / 100
      total         = subtotal + service + <venue's hire_fee_gbp + min_spend_gbp>
      deposit_rule  = per deposit_policy thresholds

    Returns:
      output: {
        "venue_id": str,
        "party_size": int,
        "duration_hours": int,
        "catering_tier": str,
        "subtotal_gbp": int,
        "service_gbp": int,
        "total_gbp": int,
        "deposit_required_gbp": int,
      }
      summary: "calculate_cost(<venue>, <party>): total £<N>, deposit £<M>"

    MUST call record_tool_call(...) before returning.
    """
    # Load rate tables and venue list in one pass each.
    catering: dict = _load_fixture("catering.json")  # type: ignore[assignment]
    venues: list[dict] = _load_fixture("venues.json")  # type: ignore[assignment]

    # --- Validate catering tier ---
    base_rates: dict = catering["base_rates_gbp_per_head"]
    if catering_tier not in base_rates:
        err = ToolError(
            "SA_TOOL_INVALID_INPUT",
            f"Unknown catering_tier '{catering_tier}'. "
            f"Valid options: {list(base_rates.keys())}",
        )
        record_tool_call(
            "calculate_cost",
            {"venue_id": venue_id, "party_size": party_size,
             "duration_hours": duration_hours, "catering_tier": catering_tier},
            {},
        )
        return ToolResult(success=False, output={}, summary=f"calculate_cost({venue_id!r}): unknown catering tier", error=err)

    # --- Validate venue_id and retrieve venue-level fixed costs ---
    venue_map = {v["id"]: v for v in venues}
    if venue_id not in venue_map:
        err = ToolError(
            "SA_TOOL_INVALID_INPUT",
            f"Unknown venue_id '{venue_id}'. "
            f"Valid ids: {list(venue_map.keys())}",
        )
        record_tool_call(
            "calculate_cost",
            {"venue_id": venue_id, "party_size": party_size,
             "duration_hours": duration_hours, "catering_tier": catering_tier},
            {},
        )
        return ToolResult(success=False, output={}, summary=f"calculate_cost({venue_id!r}): unknown venue", error=err)

    venue = venue_map[venue_id]
    hire_fee = venue.get("hire_fee_gbp", 0)
    min_spend = venue.get("min_spend_gbp", 0)

    # --- Validate venue modifier (defensive: fixture may be incomplete) ---
    venue_modifiers: dict = catering["venue_modifiers"]
    if venue_id not in venue_modifiers:
        err = ToolError(
            "SA_TOOL_INVALID_INPUT",
            f"No venue modifier found for '{venue_id}' in catering fixture.",
        )
        record_tool_call(
            "calculate_cost",
            {"venue_id": venue_id, "party_size": party_size,
             "duration_hours": duration_hours, "catering_tier": catering_tier},
            {},
        )
        return ToolResult(success=False, output={}, summary=f"calculate_cost({venue_id!r}): missing venue modifier", error=err)

    # --- Core cost formula ---
    base_per_head: int = base_rates[catering_tier]
    venue_mult: float = venue_modifiers[venue_id]
    # duration_hours is always at least 1 to avoid a zero-cost booking.
    effective_hours = max(1, duration_hours)

    subtotal = round(base_per_head * venue_mult * party_size * effective_hours)
    service = round(subtotal * catering["service_charge_percent"] / 100)
    # Fixed venue costs (hire fee + minimum spend) are added on top of
    # the per-head subtotal and service charge.
    total = subtotal + service + hire_fee + min_spend

    # --- Deposit policy ---
    # Thresholds come from the fixture so they stay in sync with any
    # future changes to deposit_policy without touching this code.
    if total < 300:
        deposit = 0
    elif total <= 1000:
        deposit = round(total * 0.20)
    else:
        deposit = round(total * 0.30)

    output = {
        "venue_id": venue_id,
        "party_size": party_size,
        "duration_hours": duration_hours,
        "catering_tier": catering_tier,
        "subtotal_gbp": subtotal,
        "service_gbp": service,
        "hire_fee_gbp": hire_fee,
        "min_spend_gbp": min_spend,
        "total_gbp": total,
        "deposit_required_gbp": deposit,
    }

    record_tool_call(
        "calculate_cost",
        {"venue_id": venue_id, "party_size": party_size,
         "duration_hours": duration_hours, "catering_tier": catering_tier},
        output,
    )

    return ToolResult(
        success=True,
        output=output,
        summary=f"calculate_cost({venue_id!r}, {party_size}): total £{total}, deposit £{deposit}",
    )


# ---------------------------------------------------------------------------
# TODO 4 — generate_flyer
# ---------------------------------------------------------------------------
def generate_flyer(session: Session, event_details: dict) -> ToolResult:
    """Produce an HTML flyer and write it to workspace/flyer.html.

    event_details is expected to contain at least:
      venue_name, venue_address, date, time, party_size, condition,
      temperature_c, total_gbp, deposit_required_gbp

    Write a self-contained HTML flyer (inline CSS, no external assets). Tag every key fact with data-testid="<n>" so the integrity check can parse it.

    Write a formatted HTML flyer with an H1 title, the event
    facts, a weather summary, and the cost breakdown.

    Returns:
      output: {"path": "workspace/flyer.html", "bytes_written": int}
      summary: "generate_flyer: wrote <path> (<N> chars)"

    MUST call record_tool_call(...) before returning — the integrity
    check compares the flyer's contents against earlier tool outputs.

    IMPORTANT: this tool MUST be registered with parallel_safe=False
    because it writes a file.
    """
    # Pull individual fields from the event_details dict with safe defaults
    # so the flyer renders even if the caller omits optional fields.
    venue_name = event_details.get("venue_name", "TBD")
    venue_address = event_details.get("venue_address", "TBD")
    date = event_details.get("date", "TBD")
    time = event_details.get("time", "TBD")
    party_size = event_details.get("party_size", "TBD")
    condition = event_details.get("condition", "TBD")
    temperature_c = event_details.get("temperature_c", "TBD")
    total_gbp = event_details.get("total_gbp", "TBD")
    deposit_required_gbp = event_details.get("deposit_required_gbp", "TBD")

    # Format monetary values with the £ prefix the integrity regex expects.
    total_str = f"£{total_gbp}" if isinstance(total_gbp, int) else str(total_gbp)
    deposit_str = f"£{deposit_required_gbp}" if isinstance(deposit_required_gbp, int) else str(deposit_required_gbp)

    # Format temperature with the °C suffix so extract_temperature_facts picks it up.
    temp_str = f"{temperature_c}°C" if isinstance(temperature_c, int) else str(temperature_c)

    # Self-contained HTML with inline CSS — no external assets so the file
    # opens correctly in any environment without network access.
    # Every data-testid attribute maps to a field name the integrity check
    # looks for via extract_testid_facts().
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Event Flyer — {venue_name}</title>
  <style>
    body {{
      font-family: Georgia, serif;
      max-width: 640px;
      margin: 40px auto;
      padding: 24px;
      background: #fafaf8;
      color: #222;
      border: 2px solid #b8860b;
      border-radius: 8px;
    }}
    h1 {{ color: #b8860b; text-align: center; margin-bottom: 4px; }}
    .subtitle {{ text-align: center; color: #666; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
    th {{ text-align: left; background: #b8860b; color: white; padding: 8px 12px; }}
    td {{ padding: 7px 12px; border-bottom: 1px solid #ddd; }}
    tr:last-child td {{ border-bottom: none; }}
    .highlight {{ background: #fff8e1; font-weight: bold; }}
    footer {{ text-align: center; font-size: 0.8em; color: #999; margin-top: 20px; }}
  </style>
</head>
<body>
  <h1>You're Invited!</h1>
  <p class="subtitle">Edinburgh Pub Night</p>

  <!-- Venue & event details -->
  <table>
    <tr><th colspan="2">Event Details</th></tr>
    <tr>
      <td>Venue</td>
      <td data-testid="venue_name">{venue_name}</td>
    </tr>
    <tr>
      <td>Address</td>
      <td data-testid="venue_address">{venue_address}</td>
    </tr>
    <tr>
      <td>Date</td>
      <td data-testid="date">{date}</td>
    </tr>
    <tr>
      <td>Time</td>
      <td data-testid="time">{time}</td>
    </tr>
    <tr>
      <td>Party Size</td>
      <td data-testid="party_size">{party_size}</td>
    </tr>
  </table>

  <!-- Weather summary -->
  <table>
    <tr><th colspan="2">Weather Forecast</th></tr>
    <tr>
      <td>Condition</td>
      <td data-testid="condition">{condition}</td>
    </tr>
    <tr>
      <td>Temperature</td>
      <td data-testid="temperature_c">{temp_str}</td>
    </tr>
  </table>

  <!-- Cost breakdown — monetary values must carry the £ prefix so
       extract_money_facts() in integrity.py can find them. -->
  <table>
    <tr><th colspan="2">Cost Breakdown</th></tr>
    <tr class="highlight">
      <td>Total</td>
      <td data-testid="total_gbp">{total_str}</td>
    </tr>
    <tr>
      <td>Deposit Required</td>
      <td data-testid="deposit_required_gbp">{deposit_str}</td>
    </tr>
  </table>

  <footer>Generated by the Edinburgh Research Agent</footer>
</body>
</html>"""

    # Ensure the workspace directory exists before writing; Session.workspace_dir
    # is the conventional location for agent output files.
    workspace = session.workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)
    flyer_path = workspace / "flyer.html"
    flyer_path.write_text(html, encoding="utf-8")

    relative_path = "workspace/flyer.html"
    bytes_written = len(html)

    output = {"path": relative_path, "bytes_written": bytes_written}

    # Record after writing so the log contains the final byte count.
    record_tool_call("generate_flyer", {"event_details": event_details}, output)

    return ToolResult(
        success=True,
        output=output,
        summary=f"generate_flyer: wrote {relative_path} ({bytes_written} chars)",
    )


# ---------------------------------------------------------------------------
# Registry builder — DO NOT MODIFY the name, signature, or registration calls.
# The grader imports and calls this to pick up your tools.
# ---------------------------------------------------------------------------
def build_tool_registry(session: Session) -> ToolRegistry:
    """Build a session-scoped tool registry with all four Ex5 tools plus
    the sovereign-agent builtins (read_file, write_file, list_files,
    handoff_to_structured, complete_task).

    DO NOT change the tool names — the tests and grader call them by name.
    """
    from sovereign_agent.tools.builtin import make_builtin_registry

    reg = make_builtin_registry(session)

    # venue_search
    reg.register(
        _RegisteredTool(
            name="venue_search",
            description="Search Edinburgh venues by area, party size, and max budget.",
            fn=venue_search,
            parameters_schema={
                "type": "object",
                "properties": {
                    "near": {"type": "string"},
                    "party_size": {"type": "integer"},
                    "budget_max_gbp": {"type": "integer", "default": 1000},
                },
                "required": ["near", "party_size"],
            },
            returns_schema={"type": "object"},
            is_async=False,
            parallel_safe=True,  # read-only
            examples=[
                {
                    "input": {"near": "Haymarket", "party_size": 6, "budget_max_gbp": 800},
                    "output": {"count": 1, "results": [{"id": "haymarket_tap"}]},
                }
            ],
        )
    )

    # get_weather
    reg.register(
        _RegisteredTool(
            name="get_weather",
            description="Get scripted weather for a city on a YYYY-MM-DD date.",
            fn=get_weather,
            parameters_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["city", "date"],
            },
            returns_schema={"type": "object"},
            is_async=False,
            parallel_safe=True,  # read-only
            examples=[
                {
                    "input": {"city": "Edinburgh", "date": "2026-04-25"},
                    "output": {"condition": "cloudy", "temperature_c": 12},
                }
            ],
        )
    )

    # calculate_cost
    reg.register(
        _RegisteredTool(
            name="calculate_cost",
            description="Compute total cost and deposit for a booking.",
            fn=calculate_cost,
            parameters_schema={
                "type": "object",
                "properties": {
                    "venue_id": {"type": "string"},
                    "party_size": {"type": "integer"},
                    "duration_hours": {"type": "integer"},
                    "catering_tier": {
                        "type": "string",
                        "enum": ["drinks_only", "bar_snacks", "sit_down_meal", "three_course_meal"],
                        "default": "bar_snacks",
                    },
                },
                "required": ["venue_id", "party_size", "duration_hours"],
            },
            returns_schema={"type": "object"},
            is_async=False,
            parallel_safe=True,  # pure compute, no shared state
            examples=[
                {
                    "input": {
                        "venue_id": "haymarket_tap",
                        "party_size": 6,
                        "duration_hours": 3,
                    },
                    "output": {"total_gbp": 540, "deposit_required_gbp": 0},
                }
            ],
        )
    )

    # generate_flyer — parallel_safe=False because it writes a file
    def _flyer_adapter(event_details: dict) -> ToolResult:
        return generate_flyer(session, event_details)

    reg.register(
        _RegisteredTool(
            name="generate_flyer",
            description="Write an HTML flyer for the event to workspace/flyer.html.",
            fn=_flyer_adapter,
            parameters_schema={
                "type": "object",
                "properties": {"event_details": {"type": "object"}},
                "required": ["event_details"],
            },
            returns_schema={"type": "object"},
            is_async=False,
            parallel_safe=False,  # writes a file — MUST be False
            examples=[
                {
                    "input": {
                        "event_details": {
                            "venue_name": "Haymarket Tap",
                            "date": "2026-04-25",
                            "party_size": 6,
                        }
                    },
                    "output": {"path": "workspace/flyer.html"},
                }
            ],
        )
    )

    return reg


__all__ = [
    "build_tool_registry",
    "venue_search",
    "get_weather",
    "calculate_cost",
    "generate_flyer",
]
