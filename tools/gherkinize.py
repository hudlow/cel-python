import argparse
from io import open
import logging
from os import path
from pathlib import Path
import sys
from typing import List, Optional, Self
from jinja2 import Environment, FileSystemLoader
from cel.expr import checked_pb2, value_pb2
from cel.expr.conformance.test import simple_pb2
from cel.expr.conformance.proto2 import (
    test_all_types_pb2 as proto2_test_all_types, # noqa
    test_all_types_extensions_pb2 as proto2_test_all_types_extensions, # noqa
)
from cel.expr.conformance.proto3 import test_all_types_pb2 as proto3_test_all_types  # noqa
from google.protobuf import (
    any_pb2,
    descriptor_pool,
    descriptor, # noqa
    duration_pb2, # noqa
    message_factory,
    message,
    struct_pb2, # noqa
    symbol_database, # noqa
    text_format,
    timestamp_pb2, # noqa
    wrappers_pb2,
)

env = Environment(
    loader=FileSystemLoader(path.dirname(__file__)),
    trim_blocks=True,
)
template = env.get_template("gherkin.feature.jinja")
logger = logging.getLogger("gherkinize")

pool = descriptor_pool.Default()

class Result:
    def __init__(self, source: simple_pb2.SimpleTest):
        self.source = source
        self.kind = self.source.WhichOneof("result_matcher")

        if self.kind == "value":
            self.literal = str(CelValue.from_proto(self.source.value))
        elif self.kind == "eval_error":
            self.literal = repr(self.source.eval_error.errors[0].message)
        else:
            raise Exception(f'Unable to interpret result kind {self.kind!r}')

    def __str__(self):
        return self.literal


class CelValue:
    def __init__(self, value):
        self.value = value

    @staticmethod
    def is_aliased(alias: str):
        return (alias in [])

    @staticmethod
    def get_class_by_alias(alias: str, base = None):
        base_class = base if base else CelValue
        classes = base_class.__subclasses__()

        for child in classes:
            if child.is_aliased(alias):
                return child
            else:
                grandchild = child.get_class_by_alias(alias, child)

                if grandchild is not None:
                    return grandchild

        if base_class is CelValue:
            raise Exception(f"Unable to locate CEL value class for alias {alias!r}")
        else:
            return None

    @staticmethod
    def from_proto(source: value_pb2.Value):
        value_kind = source.WhichOneof("kind")
        return CelValue.get_class_by_alias(value_kind)(getattr(source, value_kind))


class CelType(CelValue):
    def __init__(self, value):
        super().__init__(value)
        self.prefix = "celpy.celtypes."

        if isinstance(value, checked_pb2.Decl):
            self.__from_decl(value)
        elif isinstance(value, value_pb2.Value):
            self.__from_cel_value(value)
        elif isinstance(value, str):
            self.prefix = ""
            self.name = value
        else:
            raise Exception(f'Unable to interpret type from {value.DESCRIPTOR.fullName} message')

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["type_value"])

    def __from_decl(self, value: checked_pb2.Decl):
        decl_kind = self.value.WhichOneof("decl_kind")

        if decl_kind == "ident":
            type = self.value.ident.type
            type_kind = type.WhichOneof("type_kind")

            if type_kind == "primitive":
                primitive_kind = checked_pb2.Type.PrimitiveType.Name(type.primitive)

                if primitive_kind == "BOOL":
                    self.name = "BoolType"
                if primitive_kind == "INT64":
                    self.name = "IntType"
                if primitive_kind == "UINT64":
                    self.name = "UintType"
                if primitive_kind == "DOUBLE":
                    self.name = "DoubleType"
                if primitive_kind == "STRING":
                    self.name = "StringType"
                if primitive_kind == "BYTES":
                    self.name = "BytesType"
            elif type_kind == "null":
                self.prefix = ""
                self.name = "None"
            elif type_kind == "message_type":
                self.prefix = ""
                self.name = pool.FindMessageTypeByName(type.message_type).name
            elif type_kind in ["map_type"]:
                self.name = "MapType"
            elif type_kind in ["list_type"]:
                self.name = "ListType"
            else:
                raise Exception(f'Unable to interpret type kind "{type_kind}"')
        else:
            raise Exception(f'Unable to interpret declaration kind "{decl_kind}"')

    def __from_cel_value(self, source: value_pb2.Value):
        type_value = self.source.type_value
        self.prefix = ""
        if type_value == "bool":
            self.name = "BoolType"
        elif type_value == "bytes":
            self.name = "BytesType"
        elif type_value == "double":
            self.name = "DoubleType"
        elif type_value == "int":
            self.name = "IntType"
        elif type_value == "list":
            self.name = "ListType"
        elif type_value == "map":
            self.name = "MapType"
        elif type_value == "null_type":
            self.name = "None"
        elif type_value == "string":
            self.name = "StringType"
        elif type_value == "type":
            self.name = "TypeType"
        elif type_value == "uint":
            self.name = "UintType"
        elif type_value == "google.protobuf.Duration":
            self.name = "DurationType"
        else:
            self.name = self.source.type_value

    def __str__(self):
        return self.prefix + self.name

class CelExprValue:
    def __init__(self, source: value_pb2.Value):
        self.source = source
        expr_value_kind = self.source.WhichOneof("kind")

        if expr_value_kind == "value":
            self.literal = str(CelValue.from_proto(self.source.value))
        else:
            raise Exception(f'Unable to interpret CEL expression value kind "{expr_value_kind}"')

    def __str__(self):
        return self.literal

class CelPrimitive(CelValue):
    def __str__(self):
        return f"{self.type}(source={self.value!r})"

class CelInt(CelPrimitive):
    type = "celpy.celtypes.IntType"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["int64_value"])

class CelUint(CelPrimitive):
    type = "celpy.celtypes.UintType"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["uint64_value"])

class CelDouble(CelPrimitive):
    type = "celpy.celtypes.DoubleType"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["double_value"])

class CelBool(CelPrimitive):
    type = "celpy.celtypes.BoolType"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["bool_value"])

class CelString(CelPrimitive):
    type = "celpy.celtypes.StringType"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["string_value"])

class CelBytes(CelPrimitive):
    type = "celpy.celtypes.BytesType"

    def __init__(self, value):
        super().__init__(value, "")

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["bytes_value"])

class CelEnum(CelPrimitive):
    def __init__(self, value):
        raise Exception("Enums not yet supported")

class CelNull(CelValue):
    type = "None"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["null_value"])

    def __str__(self):
        return self.type

class CelList(CelValue):
    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["list_value"])

    def __str__(self):
        return f"[{', '.join([str(CelValue.from_proto(v)) for v in self.value.values])}]"

class CelMap(CelValue):
    type = "celpy.celtypes.MapType"

    def __init__(self, value):
        super().__init__(value)

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["map_value"])

    def __str__(self):
        return f"{self.type}({{{', '.join([f'{CelValue.from_proto(e.key)}: {CelValue.from_proto(e.value)}' for e in self.value.entries])}}})"

class CelObject(CelValue):
    def __init__(self, value):
        super().__init__(ProtoAny(value))

    @staticmethod
    def is_aliased(alias: str):
        return (alias in ["object_value"])

    def __str__(self):
        return str(self.value)

class ProtoValue:
    def __init__(self, source: value_pb2.Value):
        self.source = source
        value_kind = self.source.WhichOneof("kind")

        if value_kind == "null_value":
            self.literal = "None"
        elif value_kind == "number_value":
            self.literal = f"DoubleType(source={self.source.number_value!r})"
        elif value_kind == "string_value":
            self.literal = f"StringType(source={self.source.string_value!r})"
        elif value_kind == "bool_value":
            self.literal = f"BoolType(source={self.source.bool_value!r})"
        elif value_kind == "struct_value":
            self.literal = str(ProtoStruct(self.source.struct_value))
        elif value_kind == "list_value":
            self.literal = str(ProtoList(self.source.list_value))
        else:
            raise Exception(f'Unable to interpret value kind "{value_kind}"')

    def __str__(self):
        return self.literal

class ProtoAny:
    def __init__(self, source: any_pb2.Any):
        self.source = source
        type_name = self.source.type_url.split("/")[-1]
        desc = pool.FindMessageTypeByName(type_name)
        message_value = message_factory.GetMessageClass(desc)()
        self.source.Unpack(message_value)
        if ProtoWrapper.is_wrapper(message_value):
            self.literal = str(ProtoWrapper(message_value))
        else:
            self.literal = str(ProtoMessage(message_value))

    def __str__(self):
        return str(self.literal)


class ProtoMessage:
    def __init__(self, source: message.Message, name_override = None):
        self.source = source
        name = name_override if name_override is not None else self.source.DESCRIPTOR.name
        fieldLiterals = []
        fields = self.source.ListFields()
        for desc, value in fields:
            if ProtoWrapper.is_wrapper(value):
                fieldLiterals.append(f"{desc.name}={ProtoWrapper(value)}")
            else:
                fieldLiterals.append(f"{desc.name}={value}")
        self.literal = f"{name}({", ".join(fieldLiterals)})"

    def __str__(self):
        return self.literal


class ProtoWrapper:
    def __init__(self, source: message.Message):
        self.source = source
        wrapper_kind = self.source.DESCRIPTOR.name

        if wrapper_kind in ["Int32Value", "Int64Value"]:
            self.literal = f"IntType(source={self.source.value!r})"
        elif wrapper_kind in ["UInt32Value", "UInt64Value"]:
            self.literal = f"UintType(source={self.source.value!r})"
        elif wrapper_kind in ["DoubleValue", "FloatValue"]:
            self.literal = f"DoubleType(source={self.source.value!r})"
        elif wrapper_kind in ["BoolValue"]:
            self.literal = f"BoolType(source={self.source.value!r})"
        elif wrapper_kind in ["StringValue"]:
            self.literal = f"StringType(source={self.source.value!r})"
        elif wrapper_kind in ["BytesValue"]:
            self.literal = f"BytesType(source={self.source.value!r})"
        elif wrapper_kind in ["ListValue"]:
            self.literal = f"[{', '.join([str(ProtoValue(v)) for v in self.source.values])}]"
        elif wrapper_kind == "Struct":
            self.literal = str(ProtoStruct(self.source))
        elif wrapper_kind == "Value":
            self.literal = str(ProtoValue(self.source))
        elif wrapper_kind == "Any":
            self.literal = str(ProtoAny(self.source))
        elif wrapper_kind == "Duration":
            self.literal = str(ProtoMessage(self.source, "DurationType"))
        elif wrapper_kind == "TestAllTypes":
            self.literal = str(ProtoMessage(self.source, "TestAllTypes"))
        # elif wrapper_kind == "Timestamp":
        else:
            raise Exception(f'Unable to interpret wrapper kind "{wrapper_kind}"')

    @staticmethod
    def is_wrapper(message):
        return (
            hasattr(message, "DESCRIPTOR")
            and wrappers_pb2.DESCRIPTOR.pool.FindMessageTypeByName(
                message.DESCRIPTOR.full_name
            )
            is not None
        )

    def __str__(self):
        return self.literal


class ProtoStruct:
    def __init__(self, source: struct_pb2.Struct):
        self.source: struct_pb2.Struct = source
        self.literal = f"MapType({{{', '.join([f'{k!r}: {ProtoValue(self.source.fields[k])!s}' for k in self.source.fields])}}})"

    def __str__(self):
        return self.literal


class ProtoList:
    def __init__(self, source: struct_pb2.ListValue):
        self.source = source
        self.literal = f"[{', '.join([str(ProtoValue(v)) for v in self.source.values])}]"

    def __str__(self):
        return self.literal


class Proxy:
    @property
    def name(self):
        return self.source.name

    @property
    def description(self):
        return self.source.description


class Scenario(Proxy):
    def __init__(self, source: simple_pb2.SimpleTest):
        logger.debug(f"Scenario {source.name}")
        self.source = source
        self.preconditions = []
        self.events = []
        self.outcomes = []

        if self.source.disable_macros:
            self.given("disable_macros parameter is True")
        # if self.source.disable_check:
        #     self.given("disable_check parameter is True")
        for type_env in self.source.type_env:
            self.given(f'type_env parameter "{type_env.name}" is {CelType(type_env)}')
        for key in self.source.bindings.keys():
            self.given(f'bindings parameter "{key}" is {CelExprValue(self.source.bindings[key])}')
        if self.source.container:
            self.given(f"container is {self.source.container!r}")

        self.when(f"CEL expression {self.source.expr!r} is evaluated")

        result = Result(self.source)
        self.then(f"{result.kind} is {result}")

    def given(self, precondition: str) -> Self:
        self.preconditions.append(precondition)
        return self

    def when(self, event: str) -> Self:
        self.events.append(event)
        return self

    def then(self, event: str) -> Self:
        self.outcomes.append(event)
        return self

    @property
    def steps(self) -> List[str]:
        steps = []
        if len(self.preconditions) > 0:
            steps.append(f"Given {self.preconditions[0]}")
            steps.extend([f"and {p}" for p in self.preconditions[1:]])
        if len(self.events) > 0:
            steps.append(f"When {self.events[0]}")
            steps.extend([f"and {e}" for e in self.events[1:]])
        if len(self.outcomes) > 0:
            steps.append(f"Then {self.outcomes[0]}")
            steps.extend([f"and {o}" for o in self.outcomes[1:]])

        return steps


class Section(Proxy):
    def __init__(self, source: simple_pb2.SimpleTestSection):
        logger.debug(f"Section {source.name}")
        self.source = source
        self.scenarios = []
        for test in source.test:
            try:
                self.scenarios.append(Scenario(test))
            except Exception as e:
                logger.warning(f"Skipping scenario {test.name} because of error: {e}")


class Feature(Proxy):
    def __init__(self, source: simple_pb2.SimpleTestFile):
        self.source = source
        self.sections = [Section(s) for s in source.section]

    @staticmethod
    def from_text_proto(path: Path) -> Self:
        logger.debug(f"Reading from {path}...")
        with open(path, encoding="utf_8") as file_handle:
            text = file_handle.read()
            file = simple_pb2.SimpleTestFile()
            logger.debug(f"Parsing {path}...")
            text_format.Parse(text, file)
            return Feature(file)

    def to_gherkin(self, path: Optional[Path]) -> None:
        logger.debug("Rendering to gherkin...")
        gherkin = template.render(feature=self)

        if path:
            logger.debug(f"Writing to {path}...")
            with open(path, "w", encoding="utf_8") as file_handle:
                file_handle.write(gherkin)
        else:
            print(gherkin)


def get_options(argv: List[str] = sys.argv[1:]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        dest="log_level",
        action="store_const",
        const=logging.DEBUG,
        default=logging.INFO,
    )
    parser.add_argument(
        "-s", "--silent", dest="log_level", action="store_const", const=logging.ERROR
    )
    parser.add_argument(
        "-o",
        "--output",
        action="store",
        type=Path,
        default=None,
        help="output file (default is stdout)",
    )
    parser.add_argument(
        "source",
        action="store",
        nargs="?",
        type=Path,
        help=".textproto file to convert",
    )
    options = parser.parse_args(argv)
    return options


if __name__ == "__main__":
    options = get_options()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().setLevel(options.log_level)

    feature = Feature.from_text_proto(options.source)
    feature.to_gherkin(options.output)
