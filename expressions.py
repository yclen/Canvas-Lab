"""expressions.py — safe math equations for the Function object.

    eq = Equation("2*P + sin(t)")
    eq.variables          # ["P"]  (names to link, in the order they first appear)
    eq.error              # "" when it parsed, else why not
    eq.evaluate({"P": 3, "t": 0.5})   # -> float; raises EquationError

The text is parsed with Python's ast and checked against a whitelist (numbers,
names, + - * / % ** and ^, comparisons, and/or/not, `a if c else b`, and the
functions in FUNCTIONS) before it's compiled, so nothing else can run. A
leading "y =" is ignored, and "2P" means 2*P. Numbers are floats, so 9**9**9 overflows instead of
hanging. No pygame here — it can be tested on its own.
"""

import ast
import math
import re

# Implicit multiply, like the neuron equations: "2P" -> 2*P, "3(t+1)" -> 3*(t+1),
# "(a)(b)" -> (a)*(b). Not inside names ("log10(x)", "x2"), not "1e-3", never
# before a keyword ("3 and t").
_IMPLICIT = re.compile(r"(\b\d+(?:\.\d*)?|\))\s*"
                       r"(?=(?!(?:and|or|not|if|else)\b)(?![eE][+-]?\d)[A-Za-z_(])")


def _sign(x):
    return (x > 0) - (x < 0)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


FUNCTIONS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log10": math.log10, "log2": math.log2,
    "abs": abs, "min": min, "max": max, "round": round, "floor": math.floor, "ceil": math.ceil,
    "sign": _sign, "clamp": _clamp,
}
CONSTANTS = {"pi": math.pi, "e": math.e}
BUILTINS = {"t"}                 # supplied by the caller each evaluation (seconds), never linked
RESERVED = set(FUNCTIONS) | set(CONSTANTS) | BUILTINS

_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp,
          ast.Call, ast.Name, ast.Load, ast.Constant,
          ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
          ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
          ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class EquationError(Exception):
    """Why an equation can't give a number (shown to the user as-is)."""


class _Floats(ast.NodeTransformer):
    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(float(node.value)), node)


class Equation:
    def __init__(self, text):
        self.text = text
        self.variables = []
        self.error = ""
        self._code = None
        source = text.strip()
        if source[:1] in ("y", "Y") and source[1:].lstrip().startswith("=") \
                and not source[1:].lstrip().startswith("=="):
            source = source[1:].lstrip()[1:]          # "y = ..." -> "..."
        source = _IMPLICIT.sub(r"\1*", source.replace("^", "**"))
        if not source.strip():
            self.error = "no equation"
            return
        try:
            tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError:
            self.error = "can't read that equation"
            return
        names = []
        for node in ast.walk(tree):
            if not isinstance(node, _NODES):
                self.error = f"not allowed: {type(node).__name__}"
                return
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                self.error = "only numbers are allowed"
                return
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                    name = getattr(node.func, "id", "?")
                    self.error = f"unknown function {name}()"
                    return
                if node.keywords:
                    self.error = f"{node.func.id}() takes plain values"
                    return
            elif isinstance(node, ast.Name) and node.id not in RESERVED:
                names.append(node)
        names.sort(key=lambda n: (n.lineno, n.col_offset))    # order they appear in the text
        self.variables = list(dict.fromkeys(n.id for n in names))
        tree = ast.fix_missing_locations(_Floats().visit(tree))
        self._code = compile(tree, "<equation>", "eval")

    def evaluate(self, values):
        """The result for {name: number} (must include every variable, and t
        if it's used). Raises EquationError with a short reason."""
        if self._code is None:
            raise EquationError(self.error)
        scope = dict(FUNCTIONS, **CONSTANTS)
        scope.update(values)
        try:
            result = eval(self._code, {"__builtins__": {}}, scope)
        except ZeroDivisionError:
            raise EquationError("divide by zero")
        except OverflowError:
            raise EquationError("too large")
        except ValueError:
            raise EquationError("outside a function's range (e.g. sqrt of -1)")
        except TypeError:
            raise EquationError("wrong number of values in a function")
        except NameError as exc:
            raise EquationError(f"{exc.name} has no value")
        if isinstance(result, complex):
            raise EquationError("not a real number")
        result = float(result)
        if not math.isfinite(result):
            raise EquationError("not a finite number")
        return result
