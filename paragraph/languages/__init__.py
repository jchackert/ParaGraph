"""Per-language extraction pieces: configs, import handlers, walk helpers.

Split out of paragraph.extract; the generic extractor and public API live there.
"""
from ._util import _make_adders, _make_id, _parse_source, _read_text
from .configs import (
    LanguageConfig,
    _C_CONFIG,
    _CPP_CONFIG,
    _CSHARP_CONFIG,
    _JAVA_CONFIG,
    _JS_CONFIG,
    _KOTLIN_CONFIG,
    _LUA_CONFIG,
    _PHP_CONFIG,
    _PYTHON_CONFIG,
    _RUBY_CONFIG,
    _SCALA_CONFIG,
    _SWIFT_CONFIG,
    _TS_CONFIG,
)
from .imports import (
    _import_c,
    _import_csharp,
    _import_java,
    _import_js,
    _import_kotlin,
    _import_lua,
    _import_php,
    _import_python,
    _import_scala,
    _import_swift,
)
from .walks import (
    _csharp_extra_walk,
    _get_c_func_name,
    _get_cpp_func_name,
    _js_extra_walk,
    _swift_extra_walk,
    extract_go,
    extract_objc,
    extract_rust,
)

__all__ = [
    "LanguageConfig",
    "extract_go",
    "extract_objc",
    "extract_rust",
]
