#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from pathlib import Path
import sys
from typing import Any, Tuple

import yaml

if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from wireviz import __version__
from wireviz.Harness import Harness
from wireviz.wv_helper import expand, open_file_read


def parse(yaml_input: str, file_out: (str, Path) = None, return_types: (None, str, Tuple[str]) = None) -> Any:
    """
    Parses yaml input string and does the high-level harness conversion

    :param yaml_input: a string containing the yaml input data
    :param file_out:
    :param return_types: if None, then returns None; if the value is a string, then a
        corresponding data format will be returned; if the value is a tuple of strings,
        then for every valid format in the `return_types` tuple, another return type
        will be generated and returned in the same order; currently supports:
         - "png" - will return the PNG data
         - "svg" - will return the SVG data
         - "harness" - will return the `Harness` instance
    """

    yaml_data = yaml.safe_load(yaml_input)

    template_connectors = {}
    template_connector_names = []
    template_cables  = {}
    template_cable_names = []

    designators_and_templates = {}
    autogenerated_designators = {}
    alternating_sections = ['connectors','cables']

    harness = Harness()

    # add items
    sections = ['connectors', 'cables', 'connections']
    types = [dict, dict, list]
    for sec, ty in zip(sections, types):
        if sec in yaml_data and type(yaml_data[sec]) == ty:
            if len(yaml_data[sec]) > 0:
                if ty == dict:
                    for key, attribs in yaml_data[sec].items():
                        # TODO: take care of this image thing
                        # The Image dataclass might need to open an image file with a relative path.
                        # image = attribs.get('image')
                        # if isinstance(image, dict):
                        #     image['gv_dir'] = Path(file_out if file_out else '').parent # Inject context

                        if sec == 'connectors':
                            template_connectors[key] = attribs
                            template_connector_names.append(key)
                        elif sec == 'cables':
                            template_cables[key] = attribs
                            template_cable_names.append(key)
            else:
                pass  # section exists but is empty
        else:  # section does not exist, create empty section
            if ty == dict:
                yaml_data[sec] = {}
            elif ty == list:
                yaml_data[sec] = []

    print('Conector templates:', template_connector_names)
    print('Cable templates:   ', template_cable_names)

    def resolve_designator(inp):
        if '.' in inp:  # generate a new instance of an item
            template, designator = inp.split('.')  # TODO: handle more than one `.`
            if designator == '':
                autogenerated_designators[template] = autogenerated_designators.get(template, 0) + 1
                designator = f'_{template}_{autogenerated_designators[template]}'
            # check if contradiction
            if designator in designators_and_templates:
                if designators_and_templates[designator] != template:
                    raise Exception(f'Trying to redefine {designator} from {designators_and_templates[designator]} to {template}')
            else:
                designators_and_templates[designator] = template
        else:
            template = inp
            designator = inp
            if designator in designators_and_templates:
                pass  # referencing an exiting connector, no need to add again
            else:
                designators_and_templates[designator] = template
        return (template, designator)

    connection_sets = yaml_data['connections']
    for connection_set in connection_sets:
        print('')

        print('connection set @0:', connection_set)

        # figure out number of parallel connections within this set
        connectioncount = []
        for entry in connection_set:
            if isinstance(entry, list):
                connectioncount.append(len(entry))
            elif isinstance(entry, dict):
                connectioncount.append(len(expand(list(entry.values())[0])))  # - X1: [1-4,6] yields 5
            else:
                connectioncount.append(None)  # strings do not reveal connectioncount

        if not any(connectioncount):
            raise Exception('No item in connection set revealed number of connections')
        print(f'Connection count: {connectioncount}')

        # check that all entries are the same length
        if len(set(filter(None, connectioncount))) > 1:
            raise Exception('All items in connection set must reference the same number of connections')

        connectioncount = list(filter(None, connectioncount))[0]

        # expand string entries to list entries of correct length
        for index, entry in enumerate(connection_set):
            print(index, entry, connectioncount)
            if isinstance(entry, str):
                connection_set[index] = [entry] * connectioncount

        print('connection set @1:', connection_set)
        print('des_temp @1:', designators_and_templates)

        # resolve all designators
        for index, entry in enumerate(connection_set):
            # print(index, ':')
            if isinstance(entry, list):
                for subindex, item in enumerate(entry):
                    template, designator = resolve_designator(item)
                    # print('list', index, subindex, item, template, designator)
                    connection_set[index][subindex] = designator
            elif isinstance(entry, dict):
                key = list(entry.keys())[0]
                template, designator = resolve_designator(key)
                value = entry[key]
                # print('dict', key, template, designator, value)
                connection_set[index] = {designator: value}
            else:
                pass  # string entries have been expanded in previous step

        print('connection set @2:', connection_set)
        print('des_temp @2:', designators_and_templates)

        # expand all pin lists
        for index, entry in enumerate(connection_set):
            if isinstance(entry, list):
                connection_set[index] = [{designator: 1} for designator in entry]
            elif isinstance(entry, dict):
                designator = list(entry.keys())[0]
                pinlist = expand(entry[designator])
                connection_set[index] = [{designator: pin} for pin in pinlist]

        print('connection set @3:', connection_set)

        # TODO: check alternating cable/connector

        # generate items
        for entry in connection_set:
            for item in entry:
                designator = list(item.keys())[0]
                template = designators_and_templates[designator]
                if designator in harness.connectors:
                    print('   ', designator, 'is an existing connector instance')
                elif template in template_connector_names:
                    print('   ', designator, 'is a new connector instance of type', template)
                    harness.add_connector(name = designator, **template_connectors[template])

                elif designator in harness.cables:
                    print('   ', designator, 'is an existing cable instance')
                elif template in template_cable_names:
                    print('   ', designator, 'is a new cable instance of type', template)
                    harness.add_cable(name = designator, **template_cables[template])

                else:
                    print(f'   Template {template} not found, neither in connectors nor in cables')


        print('TRANSPOSE!!')
        connection_set = list(map(list, zip(*connection_set)))  # transpose list
        print(connection_set)

        # actually connect components using connection list
        for index_connection, connection in enumerate(connection_set):
            print(f'  connection ic {index_connection}', connection)
            for index_item, item in enumerate(connection):
                print(f'    item ii {index_item}', item)
                designator = list(item.keys())[0]
                if designator in harness.cables:
                    print(f'    - {designator} is a known cable')
                    if index_item == 0:  # list started with a cable, no connector to join on left side
                        from_name = None
                        from_pin  = None
                    else:
                        from_name = list(connection_set[index_connection][index_item-1].keys())[0]
                        from_pin  = connection_set[index_connection][index_item-1][from_name]
                    via_name  = designator
                    via_pin   = item[designator]
                    if index_item == len(connection) - 1:  # list ends with a cable, no connector to join on right side
                        to_name   = None
                        to_pin    = None
                    else:
                        to_name   = list(connection_set[index_connection][index_item+1].keys())[0]
                        to_pin    = connection_set[index_connection][index_item+1][to_name]
                    print('    > connect ', from_name, from_pin, via_name, via_pin, to_name, to_pin)
                    harness.connect(from_name, from_pin, via_name, via_pin, to_name, to_pin)


    if "additional_bom_items" in yaml_data:
        for line in yaml_data["additional_bom_items"]:
            harness.add_bom_item(line)

    if file_out is not None:
        harness.output(filename=file_out, fmt=('png', 'svg'), view=False)

    if return_types is not None:
        returns = []
        if isinstance(return_types, str): # only one return type speficied
            return_types = [return_types]

        return_types = [t.lower() for t in return_types]

        for rt in return_types:
            if rt == 'png':
                returns.append(harness.png)
            if rt == 'svg':
                returns.append(harness.svg)
            if rt == 'harness':
                returns.append(harness)

        return tuple(returns) if len(returns) != 1 else returns[0]


def parse_file(yaml_file: str, file_out: (str, Path) = None) -> None:
    with open_file_read(yaml_file) as file:
        yaml_input = file.read()

    if not file_out:
        fn, fext = os.path.splitext(yaml_file)
        file_out = fn
    file_out = os.path.abspath(file_out)

    parse(yaml_input, file_out=file_out)


def parse_cmdline():
    parser = argparse.ArgumentParser(
        description='Generate cable and wiring harness documentation from YAML descriptions',
    )
    parser.add_argument('-V', '--version', action='version', version='%(prog)s ' + __version__)
    parser.add_argument('input_file', action='store', type=str, metavar='YAML_FILE')
    parser.add_argument('-o', '--output_file', action='store', type=str, metavar='OUTPUT')
    # Not implemented: parser.add_argument('--generate-bom', action='store_true', default=True)
    parser.add_argument('--prepend-file', action='store', type=str, metavar='YAML_FILE')
    return parser.parse_args()


def main():

    args = parse_cmdline()

    if not os.path.exists(args.input_file):
        print(f'Error: input file {args.input_file} inaccessible or does not exist, check path')
        sys.exit(1)

    with open_file_read(args.input_file) as fh:
        yaml_input = fh.read()

    if args.prepend_file:
        if not os.path.exists(args.prepend_file):
            print(f'Error: prepend input file {args.prepend_file} inaccessible or does not exist, check path')
            sys.exit(1)
        with open_file_read(args.prepend_file) as fh:
            prepend = fh.read()
            yaml_input = prepend + yaml_input

    if not args.output_file:
        file_out = args.input_file
        pre, _ = os.path.splitext(file_out)
        file_out = pre  # extension will be added by graphviz output function
    else:
        file_out = args.output_file
    file_out = os.path.abspath(file_out)

    parse(yaml_input, file_out=file_out)


if __name__ == '__main__':
    main()
