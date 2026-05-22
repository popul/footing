"""CLI : `python -m footing_agent inputs.json` → plan JSON sur stdout."""

import argparse
import json
import sys

from .agent import generate_plan, PlanValidationError
from .llm import LLMError
from .validation import check_properties


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Génère un plan d'entraînement via LM Studio local",
    )
    parser.add_argument(
        "inputs",
        type=argparse.FileType("r"),
        help="JSON inputs (distance, raceDate, targetTime, currentDistance, "
             "currentTime, startDate, weeklyKm?, daysPerWeek?, longRunDay?, gear?)",
    )
    parser.add_argument(
        "--validate-only",
        metavar="PLAN.JSON",
        help="Au lieu de générer, valide un plan existant et exit non-zero si KO",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Ne pas retry une fois si le LLM viole les propriétés",
    )
    args = parser.parse_args()

    if args.validate_only:
        with open(args.validate_only) as f:
            plan = json.load(f)
        inputs = json.load(args.inputs) if args.inputs.name != "<stdin>" else None
        errs = check_properties(plan, inputs=inputs)
        if errs:
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("OK", file=sys.stderr)
        return 0

    inputs = json.load(args.inputs)
    try:
        plan = generate_plan(inputs, retry_on_validation=not args.no_retry)
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        return 2
    except PlanValidationError as e:
        print("Plan généré mais violations :", file=sys.stderr)
        for v in e.violations:
            print(f"  - {v}", file=sys.stderr)
        return 3

    json.dump(plan, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
