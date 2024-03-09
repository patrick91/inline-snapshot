import ast
import copy
import inspect
import sys
import tokenize
from pathlib import Path
from typing import Any
from typing import Dict  # noqa
from typing import Iterator
from typing import List
from typing import overload
from typing import Tuple  # noqa
from typing import TypeVar

from executing import Source

from ._change import Change
from ._change import Replace
from ._format import format_code
from ._rewrite_code import ChangeRecorder
from ._rewrite_code import end_of
from ._rewrite_code import start_of
from ._sentinels import undefined
from ._utils import ignore_tokens
from ._utils import normalize_strings
from ._utils import simple_token
from ._utils import value_to_token


class NotImplementedYet(Exception):
    pass


snapshots = {}  # type: Dict[Tuple[int, int], Snapshot]

_active = False

_files_with_snapshots = set()


class Flags:
    """
    fix: the value needs to be changed to pass the tests
    update: the value should be updated because the token-stream has changed
    create: the snapshot is empty `snapshot()`
    trim: the snapshot contains more values than neccessary. 1 could be trimmed in `5 in snapshot([1,5])`.
    """

    def __init__(self, flags=set()):
        self.fix = "fix" in flags
        self.update = "update" in flags
        self.create = "create" in flags
        self.trim = "trim" in flags

    def change_something(self):
        return self.fix or self.update or self.create or self.trim

    def to_set(self):
        return {k for k, v in self.__dict__.items() if v}

    def __repr__(self):
        return f"Flags({self.to_set()})"


_update_flags = Flags()


def ignore_old_value():
    return _update_flags.fix or _update_flags.update


class GenericValue:
    _new_value: Any
    _old_value: Any
    _current_op = "undefined"
    _ast_node: ast.Expr
    _source: Source

    def _needs_trim(self):
        return False

    def _needs_create(self):
        return self._old_value == undefined

    def _needs_fix(self):
        raise NotImplemented

    def _ignore_old(self):
        return (
            _update_flags.fix
            or _update_flags.update
            or _update_flags.create
            or self._old_value is undefined
        )

    def _visible_value(self):
        if self._ignore_old():
            return self._new_value
        else:
            return self._old_value

    def get_result(self, flags):
        return self._old_value

    def _get_changes(self) -> List[Change]:
        raise NotImplementedYet()

    def _new_code(self):
        raise NotImplementedYet()

    def __repr__(self):
        return repr(self._visible_value())

    def _type_error(self, op):
        __tracebackhide__ = True
        raise TypeError(
            f"This snapshot cannot be use with `{op}`, because it was previously used with `{self._current_op}`"
        )

    def __eq__(self, _other):
        __tracebackhide__ = True
        self._type_error("==")

    def __le__(self, _other):
        __tracebackhide__ = True
        self._type_error("<=")

    def __ge__(self, _other):
        __tracebackhide__ = True
        self._type_error(">=")

    def __contains__(self, _other):
        __tracebackhide__ = True
        self._type_error("in")

    def __getitem__(self, _item):
        __tracebackhide__ = True
        self._type_error("snapshot[key]")


class UndecidedValue(GenericValue):
    def __init__(self, old_value, ast_node, source):
        self._old_value = old_value
        self._new_value = undefined
        self._ast_node = ast_node
        self._source = source

    def _change(self, cls):
        self.__class__ = cls

    def _needs_fix(self):
        return False

    # functions which determine the type

    def __eq__(self, other):
        self._change(EqValue)
        return self == other

    def __le__(self, other):
        self._change(MinValue)
        return self <= other

    def __ge__(self, other):
        self._change(MaxValue)
        return self >= other

    def __contains__(self, other):
        self._change(CollectionValue)
        return other in self

    def __getitem__(self, item):
        self._change(DictValue)
        return self[item]


class EqValue(GenericValue):
    _current_op = "x == snapshot"

    def __eq__(self, other):
        other = copy.deepcopy(other)

        if self._new_value is undefined:
            self._new_value = other

        return self._visible_value() == other

    def token_of_node(self, node):

        return list(
            normalize_strings(
                [
                    simple_token(t.type, t.string)
                    for t in self._source.asttokens().get_tokens(node)
                    if t.type not in ignore_tokens
                ]
            )
        )

    def _format(self, text):
        return format_code(text, Path(self._source.filename))

    def _token_to_code(self, tokens):
        return self._format(tokenize.untokenize(tokens)).strip()

    def _new_code(self):
        return self._token_to_code(value_to_token(self._new_value))

    def _get_changes(self) -> Iterator[Change]:

        assert self._old_value is not undefined

        def check(old_value, old_node, new_value):

            if (
                isinstance(old_node, ast.List)
                and isinstance(new_value, list)
                and isinstance(old_value, list)
            ):
                if len(old_value) == len(new_value) == len(old_node.elts):
                    for old_value_element, old_node_element, new_value_element in zip(
                        old_value, old_node.elts, new_value
                    ):
                        yield from check(
                            old_value_element, old_node_element, new_value_element
                        )
                    return

            elif (
                isinstance(old_node, ast.Dict)
                and isinstance(new_value, dict)
                and isinstance(old_value, dict)
            ):
                if len(old_value) == len(old_node.keys):
                    for value, node in zip(old_value.keys(), old_node.keys):
                        assert node is not None

                        try:
                            # this is just a sanity check, dicts should be ordered
                            node_value = ast.literal_eval(node)
                        except:
                            continue
                        assert node_value == value

                    same_keys = old_value.keys() & new_value.keys()
                    new_keys = new_value.keys() - old_value.keys()
                    removed_keys = old_value.keys() - new_value.keys()

                    for key, node in zip(old_value.keys(), old_node.values):
                        if key in new_value:
                            yield from check(old_value[key], node, new_value[key])

                    return

            # generic fallback
            new_token = value_to_token(new_value)

            if not old_value == new_value:
                flag = "fix"
            elif self.token_of_node(old_node) != new_token:
                flag = "update"
            else:
                return

            new_code = self._token_to_code(new_token)

            yield Replace(
                node=old_node,
                source=self._source,
                new_code=new_code,
                flag=flag,
                old_value=old_value,
                new_value=new_value,
            )

        yield from check(self._old_value, self._ast_node, self._new_value)

    def _needs_fix(self):
        return self._old_value is not undefined and self._old_value != self._new_value

    def get_result(self, flags):
        if flags.fix and self._needs_fix() or flags.create and self._needs_create():
            return self._new_value
        return self._old_value


class MinMaxValue(GenericValue):
    """Generic implementation for <=, >="""

    @staticmethod
    def cmp(a, b):
        raise NotImplemented

    def _generic_cmp(self, other):
        other = copy.deepcopy(other)

        if self._new_value is undefined:
            self._new_value = other
        else:
            self._new_value = (
                self._new_value if self.cmp(self._new_value, other) else other
            )

        return self.cmp(self._visible_value(), other)

    def _needs_trim(self):
        if self._old_value is undefined:
            return False

        return not self.cmp(self._new_value, self._old_value)

    def _needs_fix(self):
        if self._old_value is undefined:
            return False
        return not self.cmp(self._old_value, self._new_value)

    def get_result(self, flags):
        if flags.create and self._needs_create():
            return self._new_value

        if flags.fix and self._needs_fix():
            return self._new_value

        if flags.trim and self._needs_trim():
            return self._new_value

        return self._old_value


class MinValue(MinMaxValue):
    """
    handles:

    >>> snapshot(5) <= 6
    True

    >>> 6 >= snapshot(5)
    True

    """

    _current_op = "x >= snapshot"

    @staticmethod
    def cmp(a, b):
        return a <= b

    __le__ = MinMaxValue._generic_cmp


class MaxValue(MinMaxValue):
    """
    handles:

    >>> snapshot(5) >= 4
    True

    >>> 4 <= snapshot(5)
    True

    """

    _current_op = "x <= snapshot"

    @staticmethod
    def cmp(a, b):
        return a >= b

    __ge__ = MinMaxValue._generic_cmp


class CollectionValue(GenericValue):
    _current_op = "x in snapshot"

    def __contains__(self, item):
        item = copy.deepcopy(item)

        if self._new_value is undefined:
            self._new_value = [item]
        else:
            if item not in self._new_value:
                self._new_value.append(item)

        if ignore_old_value() or self._old_value is undefined:
            return True
        else:
            return item in self._old_value

    def _needs_trim(self):
        if self._old_value is undefined:
            return False
        return any(item not in self._new_value for item in self._old_value)

    def _needs_fix(self):
        if self._old_value is undefined:
            return False
        return any(item not in self._old_value for item in self._new_value)

    def get_result(self, flags):
        if (flags.fix and flags.trim) or (flags.create and self._needs_create()):
            return self._new_value

        if self._old_value is not undefined:
            if flags.fix:
                return self._old_value + [
                    v for v in self._new_value if v not in self._old_value
                ]

            if flags.trim:
                return [v for v in self._old_value if v in self._new_value]

        return self._old_value


class DictValue(GenericValue):
    _current_op = "snapshot[key]"

    def __getitem__(self, index):
        if self._new_value is undefined:
            self._new_value = {}

        old_value = self._old_value
        if old_value is undefined:
            old_value = {}

        if index not in self._new_value:
            self._new_value[index] = UndecidedValue(
                old_value.get(index, undefined), None, self._source
            )

        return self._new_value[index]

    def _needs_fix(self):
        if self._old_value is not undefined and self._new_value is not undefined:
            if any(v._needs_fix() for v in self._new_value.values()):
                return True

        return False

    def _needs_trim(self):
        if self._old_value is not undefined and self._new_value is not undefined:
            if any(v._needs_trim() for v in self._new_value.values()):
                return True

            return any(item not in self._new_value for item in self._old_value)
        return False

    def _needs_create(self):
        if super()._needs_create():
            return True

        return any(item not in self._old_value for item in self._new_value)

    def get_result(self, flags):
        result = {k: v.get_result(flags) for k, v in self._new_value.items()}

        result = {k: v for k, v in result.items() if v is not undefined}

        if not flags.trim and self._old_value is not undefined:
            for k, v in self._old_value.items():
                if k not in result:
                    result[k] = v

        return result


T = TypeVar("T")

found_snapshots = []


class ReprWrapper:
    def __init__(self, func):
        self.func = func

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    def __repr__(self):
        return self.func.__name__


_T = TypeVar("_T")


def repr_wrapper(func: _T) -> _T:
    return ReprWrapper(func)  # type: ignore


@overload
def snapshot() -> Any: ...


@overload
def snapshot(obj: T) -> T: ...


@repr_wrapper
def snapshot(obj=undefined):
    """`snapshot()` is a placeholder for some value.

    `pytest --inline-snapshot=create` will create the value which matches your conditions.

    >>> assert 5 == snapshot()
    >>> assert 5 <= snapshot()
    >>> assert 5 >= snapshot()
    >>> assert 5 in snapshot()

    `snapshot()[key]` can be used to create sub-snapshots.

    The generated value will be inserted as argument to `snapshot()`

    >>> assert 5 == snapshot(5)

    `snapshot(value)` has the semantic of an noop which returns `value`.
    """
    if not _active:
        if obj is undefined:
            raise AssertionError(
                "your snapshot is missing a value run pytest with --inline-snapshot=create"
            )
        else:
            return obj

    frame = inspect.currentframe().f_back.f_back
    expr = Source.executing(frame)

    module = inspect.getmodule(frame)
    if module is not None:
        _files_with_snapshots.add(module.__file__)

    key = id(frame.f_code), frame.f_lasti

    if key not in snapshots:
        node = expr.node
        if node is None:
            # we can run without knowing of the calling expression but we will not be able to fix code
            snapshots[key] = Snapshot(obj, None)
        else:
            assert isinstance(node.func, ast.Name)
            assert node.func.id == "snapshot"
            snapshots[key] = Snapshot(obj, expr)
        found_snapshots.append(snapshots[key])

    return snapshots[key]._value


def used_externals(tree):
    if sys.version_info < (3, 8):
        return [
            n.args[0].s
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "external"
            and n.args
            and isinstance(n.args[0], ast.Str)
        ]
    else:
        return [
            n.args[0].value
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "external"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        ]


class Snapshot:
    def __init__(self, value, expr):
        self._expr = expr
        node = expr.node.args[0] if expr is not None and expr.node.args else None
        source = expr.source if expr is not None else None
        self._value = UndecidedValue(value, node, source)
        self._uses_externals = []

    @property
    def _filename(self):
        return self._expr.source.filename

    def _format(self, text):
        return format_code(text, Path(self._filename))

    def _change(self):
        assert self._expr is not None

        tokens = list(self._expr.source.asttokens().get_tokens(self._expr.node))
        assert tokens[0].string == "snapshot"
        assert tokens[1].string == "("
        assert tokens[-1].string == ")"

        try:
            if self._value._old_value is undefined:
                if _update_flags.create:
                    new_code = self._value._new_code()
                    try:
                        ast.parse(new_code)
                    except:
                        new_code = ""
                else:
                    new_code = ""

                change = ChangeRecorder.current.new_change()
                change.set_tags("inline_snapshot")
                change.replace(
                    (end_of(tokens[1]), start_of(tokens[-1])),
                    new_code,
                    filename=self._filename,
                )
                return

            changes = self._value._get_changes()
            for change in changes:
                if change.flag in _update_flags.to_set():
                    change.apply()
            return
        except NotImplementedYet:
            pass

        change = ChangeRecorder.current.new_change()
        change.set_tags("inline_snapshot")

        needs_fix = self._value._needs_fix()
        needs_create = self._value._needs_create()
        needs_trim = self._value._needs_trim()
        needs_update = self._needs_update()

        if (
            _update_flags.update
            and needs_update
            or _update_flags.fix
            and needs_fix
            or _update_flags.create
            and needs_create
            or _update_flags.trim
            and needs_trim
        ):
            new_value = self._value.get_result(_update_flags)

            text = self._format(tokenize.untokenize(value_to_token(new_value))).strip()

            try:
                tree = ast.parse(text)
            except:
                return

            self._uses_externals = used_externals(tree)

            change.replace(
                (end_of(tokens[1]), start_of(tokens[-1])),
                text,
                filename=self._filename,
            )

    def _current_tokens(self):
        if not self._expr.node.args:
            return []

        return [
            simple_token(t.type, t.string)
            for t in self._expr.source.asttokens().get_tokens(self._expr.node.args[0])
            if t.type not in ignore_tokens
        ]

    def _needs_update(self):
        return self._expr is not None and [] != list(
            normalize_strings(self._current_tokens())
        ) != list(normalize_strings(value_to_token(self._value._old_value)))

    @property
    def _flags(self):
        s = set()
        if self._value._needs_fix():
            s.add("fix")
        if self._value._needs_trim():
            s.add("trim")
        if self._value._needs_create():
            s.add("create")
        if self._value._old_value is not undefined and self._needs_update():
            s.add("update")

        return s
