"""Import verified backup evidence only; does not connect or recover the runtime."""
import argparse
# Match the application's existing package initialization order. No app/runtime
# is constructed: importing a service class has no transport side effects.
import klipper_cnc_assistant.application  # noqa: F401
from klipper_cnc_assistant.execution.administrative_recovery import import_legacy_evidence

parser = argparse.ArgumentParser()
parser.add_argument('--data-dir', required=True)
parser.add_argument('--backup-dir', required=True)
args = parser.parse_args()
print(import_legacy_evidence(args.data_dir, args.backup_dir))
