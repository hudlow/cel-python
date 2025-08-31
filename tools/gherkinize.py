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

class CelType:
    def __init__(self, source: checked_pb2.Decl):
        self.source = source
        self.prefix = "celpy.celtypes."
        decl_kind = self.source.WhichOneof("decl_kind")

        if decl_kind == "ident":
            type = self.source.ident.type
            type_kind = type.WhichOneof("type_kind")

            if type_kind == "primitive":
                self.name = checked_pb2.Type.PrimitiveType.Name(type.primitive)
            elif type_kind == "null":
                self.prefix = ""
                self.name = "null_type"
            elif type_kind == "message_type":
                self.name = f"TypeType(value='{type.message_type}')"
            elif type_kind in ["map_type", "list_type"]:
                self.name = type_kind
            else:
                raise Exception(f'Unable to interpret type kind "{type_kind}"')
        else:
            raise Exception(f'Unable to interpret declaration kind "{decl_kind}"')

    def __str__(self):
        return self.prefix + self.name


class Result:
    def __init__(self, source: simple_pb2.SimpleTest):
        self.source = source
        self.kind = self.source.WhichOneof("result_matcher")

        if self.kind == "value":
            self.literal = str(CelValue.from_proto(self.source.value))
        elif self.kind == "eval_error":
            self.literal = self.source.eval_error.errors[0].message
        else:
            raise Exception(f'Unable to interpret result kind "{self.kind}"')

    def __str__(self):
        return self.literal


class CelValue:
    @staticmethod
    def from_proto(source: value_pb2.Value):
        value_kind = source.WhichOneof("kind")

        if value_kind in "int64_value":
            return CelInt(source.int64_value)
        elif value_kind == "uint64_value":
            return CelUint(source.uint64_value)
        elif value_kind == "double_value":
            return CelDouble(source.double_value)
        elif value_kind == "string_value":
            return CelString(source.string_value)
        elif value_kind == "bytes_value":
            return CelBytes(source.bytes_value)
        elif value_kind == "bool_value":
            return CelBool(source.bool_value)
        elif value_kind == "null_value":
            return CelNull()
        elif value_kind == "list_value":
            return CelList(source.list_value)
        elif value_kind == "map_value":
            return f"{{{', '.join([f'{CelValue.from_proto(e.key)}: {CelValue.from_proto(e.value)}' for e in source.map_value.entries])}}}"
        elif value_kind == "type_value":
            return f"TypeType(value={source.type_value!r})"
        elif value_kind == "object_value":
            return ProtoAny(source.object_value)
        else:
            raise Exception(f'Unable to interpret value kind "{value_kind}"')

class CelPrimitive(CelValue):
    def __str__(self):
        return f"{self.type}(source={self.source!r})"

class CelInt(CelPrimitive):
    def __init__(self, source):
        self.type = "IntType"
        self.source = source

class CelUint(CelPrimitive):
    def __init__(self, source):
        self.type = "UintType"
        self.source = source

class CelDouble(CelPrimitive):
    def __init__(self, source):
        self.type = "DoubleType"
        self.source = source

class CelBool(CelPrimitive):
    def __init__(self, source):
        self.type = "BoolType"
        self.source = source

class CelString(CelPrimitive):
    def __init__(self, source):
        self.type = "StringType"
        self.source = source

class CelBytes(CelPrimitive):
    def __init__(self, source):
        self.type = "BytesType"
        self.source = source

class CelNull(CelValue):
    def __str__(self):
        return "None"

class CelList(CelValue):
    def __init__(self, source):
        self.source = source

    def __str__(self):
        return f"[{', '.join([str(CelValue.from_proto(v)) for v in self.source.values])}]"

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
        messageValue = message_factory.GetMessageClass(desc)()
        logger.debug(f"unpacking {type_name!r}")
        self.source.Unpack(messageValue)
        self.literal = str(ProtoMessage(messageValue))

    def __str__(self):
        return str(self.literal)


class ProtoMessage:
    def __init__(self, source: message.Message):
        self.source = source
        fieldLiterals = []
        fields = self.source.ListFields()
        for desc, value in fields:
            if ProtoWrapper.is_wrapper(value):
                fieldLiterals.append(f"{desc.name}={ProtoWrapper(value)}")
            else:
                fieldLiterals.append(f"{desc.name}={value}")
        self.literal = f"{self.source.DESCRIPTOR.name}({", ".join(fieldLiterals)})"

    def __str__(self):
        return self.literal


class ProtoWrapper:
    def __init__(self, source: message.Message):
        self.source = source
        wrapper_kind = self.source.DESCRIPTOR.name

        if wrapper_kind in ["Int32Value", "Int64Value", "UInt32Value", "UInt64Value"]:
            self.literal = f"IntType(source={self.source.value!r})"
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
        elif wrapper_kind in ["Struct"]:
            self.literal = str(ProtoStruct(self.source))
        elif wrapper_kind in ["Value"]:
            self.literal = str(ProtoValue(self.source))
        elif wrapper_kind in ["Any"]:
            self.literal = str(ProtoAny(self.source))
        elif wrapper_kind in ["Duration", "Timestamp"]:
            self.literal = str(ProtoMessage(self.source))
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
            self.given(f'bindings parameter "{key}" is TBD')
            # self.given(f"bindings parameter {key} is {self.source.bindings[key]}")
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
        self.source = source
        self.scenarios = [Scenario(t) for t in source.test]


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
