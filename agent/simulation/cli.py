import argparse
import json
import sys
from datetime import datetime

from agent.simulation.route_loader import list_routes, load_route
from agent.simulation.state import (
    load_simulation_state,
    arm_simulation,
    clear_simulation,
    set_simulation_standby_gps,
    clear_simulation_standby_gps,
)

def _format_epoch(epoch: int | None) -> str:
    if not epoch:
        return "None"
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(epoch)


def cmd_list(args) -> int:
    routes = list_routes()

    if not routes:
        print("No simulation routes found.")
        return 0

    print("Available simulation routes:\n")
    for item in routes:
        route_id = item.get("id")
        label = item.get("label")
        profile_type = item.get("profile_type") or "unknown"
        duration_s = item.get("duration_s")
        description = item.get("description") or ""

        print(f"- {route_id}")
        print(f"  Label: {label}")
        print(f"  Profile: {profile_type}")
        print(f"  Duration: {duration_s}s")
        if description:
            print(f"  Description: {description}")
        print()

    return 0


def cmd_status(args) -> int:
    state = load_simulation_state()

    print("Current simulation state:\n")
    print(json.dumps({
        **state,
        "selected_at_text": _format_epoch(state.get("selected_at_epoch")),
    }, indent=2))

    return 0


def cmd_clear(args) -> int:
    state = clear_simulation()

    # SIMULATION:
    # Remove the standby GPS snapshot when simulation is disarmed.
    try:
        clear_simulation_standby_gps()
    except Exception as e:
        print(f"Warning: failed to clear standby GPS snapshot: {e}", file=sys.stderr)

    print("Simulation state cleared.\n")
    print(json.dumps(state, indent=2))
    return 0


def cmd_arm(args) -> int:
    route_id = args.route_id.strip()

    try:
        route = load_route(route_id)
    except Exception as e:
        print(f"Failed to load route '{route_id}': {e}", file=sys.stderr)
        return 1

    state = arm_simulation(
        scenario_id=route["id"],
        temp_offset=args.temp_offset,
        hum_offset=args.hum_offset,
        press_offset=args.press_offset,
        gas_offset=args.gas_offset,
        temp_trend=args.temp_trend,
        hum_trend=args.hum_trend,
        press_trend=args.press_trend,
        gas_trend=args.gas_trend,
    )

    # SIMULATION:
    # Publish the first route point as an idle standby GPS fix so that
    # Dashboard / Check status can already see a valid location before mission start.
    try:
        waypoints = route.get("waypoints") or []
        if waypoints:
            set_simulation_standby_gps(waypoints[0])
    except Exception as e:
        print(f"Warning: failed to publish standby GPS snapshot: {e}", file=sys.stderr)

    print("Simulation armed successfully.\n")
    print(f"Selected route: {route['id']}")
    print(f"Label: {route.get('label')}")
    print(f"Profile: {route.get('profile_type')}")
    print(f"Duration: {route.get('duration_s')}s\n")
    print(json.dumps(state, indent=2))

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EnvMon simulation control CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List available simulation routes")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Show current simulation state")
    p_status.set_defaults(func=cmd_status)

    p_clear = sub.add_parser("clear", help="Clear/disarm simulation state")
    p_clear.set_defaults(func=cmd_clear)

    p_arm = sub.add_parser("arm", help="Arm a simulation route")
    p_arm.add_argument("route_id", help="Route ID, for example: drone_field_grid")
    p_arm.add_argument("--temp-offset", type=float, default=0.0)
    p_arm.add_argument("--hum-offset", type=float, default=0.0)
    p_arm.add_argument("--press-offset", type=float, default=0.0)
    p_arm.add_argument("--gas-offset", type=float, default=0.0)
    p_arm.add_argument("--temp-trend", type=float, default=0.0)
    p_arm.add_argument("--hum-trend", type=float, default=0.0)
    p_arm.add_argument("--press-trend", type=float, default=0.0)
    p_arm.add_argument("--gas-trend", type=float, default=0.0)
    p_arm.set_defaults(func=cmd_arm)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
  