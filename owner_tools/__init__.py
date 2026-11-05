"""
Owner tools package (isolated from client builds).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 4.1.
"""

from owner_tools.key_vault import KeyVaultManager
from owner_tools.signing import ManifestSigner
from owner_tools.config_validator import ConfigValidator, ConfigValidationError
from owner_tools.release_manager import ReleaseManager
from owner_tools.token_manager import OwnerTokenManager
from owner_tools.build_manager import BuildManager, ReleaseGuardError
from owner_tools.bundle_inspector import BundleInspector, BundleAuditReport
from owner_tools.packaging import PackagingEngine, generate_spec_content

__all__ = [
    "KeyVaultManager",
    "ManifestSigner",
    "ConfigValidator",
    "ConfigValidationError",
    "ReleaseManager",
    "OwnerTokenManager",
    "BuildManager",
    "ReleaseGuardError",
    "BundleInspector",
    "BundleAuditReport",
    "PackagingEngine",
    "generate_spec_content",
]
