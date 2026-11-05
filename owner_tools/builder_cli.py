"""
Builder CLI Interface for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 4.1, 4.2, and 6.5.
Provides a comprehensive terminal command suite for store management, key operations,
and build auditing.
"""

import sys
import argparse
import secrets
from pathlib import Path

from owner_tools.key_vault import KeyVaultManager, compute_sha256_fingerprint
from owner_tools.config_validator import ConfigValidator, ConfigValidationError
from owner_tools.release_manager import ReleaseManager
from owner_tools.build_manager import BuildManager, ReleaseGuardError


def main(args=None):
    parser = argparse.ArgumentParser(description="Sims 4 DLC Archive Manager - Owner Tools CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. generate-key
    sub_gen = subparsers.add_parser("generate-key", help="Generates master Ed25519 keypair and saves to DPAPI keyring.")
    sub_gen.add_argument("--keyring-dir", type=str, default=None, help="Custom keyring directory.")

    # 2. export-vault
    sub_export = subparsers.add_parser("export-vault", help="Exports master key to AES-256-GCM encrypted .vault file.")
    sub_export.add_argument("--output", type=str, required=True, help="Destination .vault path.")
    sub_export.add_argument("--password", type=str, required=True, help="Master encryption password.")
    sub_export.add_argument("--keyring-dir", type=str, default=None, help="Custom keyring directory.")

    # 3. validate-store
    sub_val = subparsers.add_parser("validate-store", help="Validates store profile configuration and assets.")
    sub_val.add_argument("--store-dir", type=str, required=True, help="Path to store directory (e.g. configs/stores/store_alpha).")

    # 4. sign-store
    sub_sign = subparsers.add_parser("sign-store", help="Generates and signs release-manifest.json for a store.")
    sub_sign.add_argument("--store-id", type=str, required=True, help="Store identifier (e.g. store_alpha).")
    sub_sign.add_argument("--keyring-dir", type=str, default=None, help="Custom keyring directory.")
    sub_sign.add_argument("--stores-base", type=str, default="configs/stores", help="Base directory for stores.")

    # 5. verify-store
    sub_ver = subparsers.add_parser("verify-store", help="Verifies release-manifest.sig and file hashes for a store.")
    sub_ver.add_argument("--store-id", type=str, required=True, help="Store identifier.")
    sub_ver.add_argument("--keyring-dir", type=str, default=None, help="Custom keyring directory.")
    sub_ver.add_argument("--stores-base", type=str, default="configs/stores", help="Base directory for stores.")

    # 6. create-challenge
    sub_chal = subparsers.add_parser("create-challenge", help="Issues a new single-use challenge for Release Guard.")
    sub_chal.add_argument("--test-id", type=str, required=True, help="Unique test ID (e.g. int_test_20261105_01).")
    sub_chal.add_argument("--vault-path", type=str, required=True, help="Path to vault file.")
    sub_chal.add_argument("--keyring-dir", type=str, default=None, help="Custom keyring directory.")

    parsed = parser.parse_args(args)

    if parsed.command == "generate-key":
        mgr = KeyVaultManager(keyring_dir=Path(parsed.keyring_dir) if parsed.keyring_dir else None)
        priv, pub = mgr.generate_and_save_master_key()
        print(f"Master key generated and saved to {mgr.keyring_dir}")
        print(f"Public Key Fingerprint: {compute_sha256_fingerprint(pub)}")

    elif parsed.command == "export-vault":
        mgr = KeyVaultManager(keyring_dir=Path(parsed.keyring_dir) if parsed.keyring_dir else None)
        priv = mgr.load_master_private_key()
        pub = mgr.load_public_key()
        out_path = mgr.export_vault(priv, pub, parsed.password, Path(parsed.output))
        print(f"Key vault exported to: {out_path}")
        print(f"Vault Fingerprint: {compute_sha256_fingerprint(out_path.read_bytes())}")

    elif parsed.command == "validate-store":
        try:
            res = ConfigValidator.validate_store_profile(Path(parsed.store_dir))
            print(f"Store '{res['store_id']}' is valid and ready for packaging.")
        except ConfigValidationError as e:
            print(f"Validation failed: {e}", file=sys.stderr)
            sys.exit(1)

    elif parsed.command == "sign-store":
        mgr = KeyVaultManager(keyring_dir=Path(parsed.keyring_dir) if parsed.keyring_dir else None)
        priv = mgr.load_master_private_key()
        rel_mgr = ReleaseManager(stores_base_dir=Path(parsed.stores_base))
        m_path, s_path = rel_mgr.sign_and_save_store_release(parsed.store_id, priv)
        print(f"Release manifest created: {m_path}")
        print(f"Release signature created: {s_path}")

    elif parsed.command == "verify-store":
        mgr = KeyVaultManager(keyring_dir=Path(parsed.keyring_dir) if parsed.keyring_dir else None)
        pub = mgr.load_public_key()
        rel_mgr = ReleaseManager(stores_base_dir=Path(parsed.stores_base))
        valid = rel_mgr.verify_store_release(parsed.store_id, pub)
        if valid:
            print(f"Store '{parsed.store_id}' release manifest and files: OK")
        else:
            print(f"Store '{parsed.store_id}' verification FAILED!", file=sys.stderr)
            sys.exit(1)

    elif parsed.command == "create-challenge":
        mgr = KeyVaultManager(keyring_dir=Path(parsed.keyring_dir) if parsed.keyring_dir else None)
        pub = mgr.load_public_key()
        pub_fp = compute_sha256_fingerprint(pub)
        vault_fp = compute_sha256_fingerprint(Path(parsed.vault_path).read_bytes())
        challenge_hex = secrets.token_hex(32)

        bm = BuildManager()
        bm.issue_challenge(parsed.test_id, challenge_hex, pub_fp, vault_fp)
        print(f"Challenge issued successfully:")
        print(f"  Test ID: {parsed.test_id}")
        print(f"  Challenge: {challenge_hex}")
        print(f"  Public FP: {pub_fp}")
        print(f"  Vault FP: {vault_fp}")


if __name__ == "__main__":
    main()
