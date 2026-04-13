import ast
from pathlib import Path


ROOT = Path("/root/hummingbot-stack/upstream/hummingbot-api")


def _load_module_ast(relative_path: str) -> ast.Module:
    return ast.parse((ROOT / relative_path).read_text())


def _find_class(module: ast.Module, class_name: str) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"class {class_name} not found")


def _find_method(class_node: ast.ClassDef, method_name: str) -> ast.FunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            return node
    raise AssertionError(f"method {method_name} not found in {class_node.name}")


def _find_async_method(class_node: ast.ClassDef, method_name: str) -> ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name:
            return node
    raise AssertionError(f"async method {method_name} not found in {class_node.name}")


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _body_contains_timestamp_then_delegate(method_node: ast.AST, delegate_name: str) -> bool:
    seen_set_timestamp = False
    for node in ast.walk(method_node):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "_set_current_timestamp":
            seen_set_timestamp = True
        if name == delegate_name and seen_set_timestamp:
            return True
    return False


def test_accounts_interface_buy_sets_timestamp_before_buy_delegate():
    module = _load_module_ast("services/accounts_service.py")
    class_node = _find_class(module, "AccountTradingInterface")
    method_node = _find_method(class_node, "buy")
    assert _body_contains_timestamp_then_delegate(method_node, "buy")


def test_accounts_interface_sell_sets_timestamp_before_sell_delegate():
    module = _load_module_ast("services/accounts_service.py")
    class_node = _find_class(module, "AccountTradingInterface")
    method_node = _find_method(class_node, "sell")
    assert _body_contains_timestamp_then_delegate(method_node, "sell")


def test_accounts_service_place_trade_sets_timestamp_before_order_submission():
    module = _load_module_ast("services/accounts_service.py")
    class_node = _find_class(module, "AccountsService")
    method_node = _find_async_method(class_node, "place_trade")
    assert _body_contains_timestamp_then_delegate(method_node, "buy")
    assert _body_contains_timestamp_then_delegate(method_node, "sell")


def test_trading_interface_buy_sets_timestamp_before_buy_delegate():
    module = _load_module_ast("services/trading_service.py")
    class_node = _find_class(module, "AccountTradingInterface")
    method_node = _find_method(class_node, "buy")
    assert _body_contains_timestamp_then_delegate(method_node, "buy")


def test_trading_interface_sell_sets_timestamp_before_sell_delegate():
    module = _load_module_ast("services/trading_service.py")
    class_node = _find_class(module, "AccountTradingInterface")
    method_node = _find_method(class_node, "sell")
    assert _body_contains_timestamp_then_delegate(method_node, "sell")
