import argparse
from io import open
import json
import logging
from os import path
from pathlib import Path
import sys
from typing import List, Optional, Self
from cel.expr import checked_pb2
from cel.expr.conformance.test import simple_pb2
from cel.expr.conformance.proto2 import test_all_types_pb2, test_all_types_extensions_pb2 # noqa
from cel.expr.conformance.proto3 import test_all_types_pb2 # noqa
from google.protobuf import descriptor, message, text_format, duration_pb2, timestamp_pb2, wrappers_pb2 # noqa
from jinja2 import Environment, FileSystemLoader

env = Environment(
    loader=FileSystemLoader(path.dirname(__file__)),
    trim_blocks=True,
)
template = env.get_template("gherkin.feature.jinja")
logger = logging.getLogger("gherkinize")

def quote(thing: str):
    if ('"' not in thing):
        return f'"{thing}"'
    if ("'" not in thing):
        return f"'{thing}'"

    return json.dumps(thing)

class TypeType:
    def __init__(self, source: checked_pb2.Decl):
        self.source = source

        if self.source.WhichOneof("decl_kind") == "ident":
            type = self.source.ident.type
            type_kind = type.WhichOneof("type_kind")

            if type_kind == "primitive":
                self.name = checked_pb2.Type.PrimitiveType.Name(type.primitive)
            elif type_kind == "null":
                self.name = "null_type"
            elif type_kind == "map_type" or type_kind == "list_type":
                self.name = type_kind
            else:
                raise Exception(f'Unable to interpret type kind "{type_kind}"')
        else:
            raise Exception("Unable to interpret declaration")

    def __str__(self):
        return self.name

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
        if self.source.disable_check:
            self.given("disable_check parameter is True")
        for type_env in self.source.type_env:
            self.given(f'type_env parameter "{type_env.name}" is {TypeType(type_env)}')
        for key in self.source.bindings.keys():
            self.given(f'bindings parameter "{key}" is TBD')
            # self.given(f"bindings parameter {key} is {self.source.bindings[key]}")
        if self.source.container:
            self.given(f"container is {self.source.container}")

        self.when(f"CEL expression {quote(self.source.expr)} is evaluated")

        if (self.source.value):
            self.then(f"value is {self.source.value}")
        else:
            self.then(f"eval_error is {self.source.eval_error}")

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
        '-v', '--verbose',
        dest="log_level",
        action='store_const', const=logging.DEBUG, default=logging.INFO)
    parser.add_argument(
        "-s", "--silent",
        dest="log_level",
        action="store_const", const=logging.ERROR)
    parser.add_argument(
        '-o', '--output', action='store', type=Path, default=None,
        help="output file (default is stdout)"
    )
    parser.add_argument(
        'source', action='store', nargs='?', type=Path,
        help=".textproto file to convert"
    )
    options = parser.parse_args(argv)
    return options

if __name__ == "__main__":
    options = get_options()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().setLevel(options.log_level)

    feature = Feature.from_text_proto(options.source)
    feature.to_gherkin(options.output)
