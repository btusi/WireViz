#!/usr/bin/env python
# -*- coding: utf-8 -*-

from wireviz.DataClasses import Connector, Cable, MatePin, MateComponent
from graphviz import Graph
from wireviz import wv_colors, wv_helper, __version__, APP_NAME, APP_URL
from wireviz.wv_colors import get_color_hex
from wireviz.wv_helper import awg_equiv, mm2_equiv, tuplelist2tsv, \
    nested_html_table, flatten2d, index_if_list, html_line_breaks, \
    clean_whitespace, open_file_read, open_file_write, html_colorbar, \
    html_image, html_caption, manufacturer_info_field, component_table_entry, remove_links
from collections import Counter
from typing import List, Union
from pathlib import Path
import re

class Harness:

    def __init__(self):
        self.color_mode = 'SHORT'
        self.mini_bom_mode = True
        self.connectors = {}
        self.cables = {}
        self.mates = []
        self._bom = []  # Internal Cache for generated bom
        self.additional_bom_items = []

    def add_connector(self, name: str, *args, **kwargs) -> None:
        self.connectors[name] = Connector(name, *args, **kwargs)

    def add_cable(self, name: str, *args, **kwargs) -> None:
        self.cables[name] = Cable(name, *args, **kwargs)

    def add_mate_pin(self, *args, **kwargs) -> None:
        self.mates.append(MatePin(*args, **kwargs))

    def add_mate_component(self, *args, **kwargs) -> None:
        self.mates.append(MateComponent(*args, **kwargs))

    def add_bom_item(self, item: dict) -> None:
        self.additional_bom_items.append(item)

    def connect(self, from_name: str, from_pin: (int, str), via_name: str, via_pin: (int, str), to_name: str, to_pin: (int, str)) -> None:
        for (name, pin) in zip([from_name, to_name], [from_pin, to_pin]):  # check from and to connectors
            if name is not None and name in self.connectors:
                connector = self.connectors[name]
                if pin in connector.pins and pin in connector.pinlabels:
                    if connector.pins.index(pin) == connector.pinlabels.index(pin):
                        # TODO: Maybe issue a warning? It's not worthy of an exception if it's unambiguous, but maybe risky?
                        pass
                    else:
                        raise Exception(f'{name}:{pin} is defined both in pinlabels and pins, for different pins.')
                if pin in connector.pinlabels:
                    if connector.pinlabels.count(pin) > 1:
                        raise Exception(f'{name}:{pin} is defined more than once.')
                    else:
                        index = connector.pinlabels.index(pin)
                        pin = connector.pins[index] # map pin name to pin number
                        if name == from_name:
                            from_pin = pin
                        if name == to_name:
                            to_pin = pin
                if not pin in connector.pins:
                    raise Exception(f'{name}:{pin} not found.')

        self.cables[via_name].connect(from_name, from_pin, via_pin, to_name, to_pin)

    def create_graph(self) -> Graph:
        dot = Graph()
        dot.body.append(f'// Graph generated by {APP_NAME} {__version__}')
        dot.body.append(f'// {APP_URL}')
        font = 'arial'
        dot.attr('graph', rankdir='LR',
                 ranksep='2',
                 bgcolor='white',
                 nodesep='0.33',
                 fontname=font)
        dot.attr('node', shape='record',
                 style='filled',
                 fillcolor='white',
                 fontname=font)
        dot.attr('edge', style='bold',
                 fontname=font)

        # prepare ports on connectors depending on which side they will connect
        for _, cable in self.cables.items():
            for connection_color in cable.connections:
                if connection_color.from_port is not None:  # connect to left
                    self.connectors[connection_color.from_name].ports_right = True
                if connection_color.to_port is not None:  # connect to right
                    self.connectors[connection_color.to_name].ports_left = True
        for mate in self.mates:
            if isinstance(mate, MatePin):
                self.connectors[mate.from_name].ports_right = True
                self.connectors[mate.from_name].activate_pin(mate.from_port)
                self.connectors[mate.to_name].ports_left = True
                self.connectors[mate.to_name].activate_pin(mate.to_port)

        for connector in self.connectors.values():

            html = []

            rows = [[remove_links(connector.name) if connector.show_name else None],
                    [f'P/N: {remove_links(connector.pn)}' if connector.pn else None,
                     html_line_breaks(manufacturer_info_field(connector.manufacturer, connector.mpn))],
                    [html_line_breaks(connector.type),
                     html_line_breaks(connector.subtype),
                     f'{connector.pincount}-pin' if connector.show_pincount else None,
                     connector.color, html_colorbar(connector.color)],
                    '<!-- connector table -->' if connector.style != 'simple' else None,
                    [html_image(connector.image)],
                    [html_caption(connector.image)]]
            rows.extend(self.get_additional_component_table(connector))
            rows.append([html_line_breaks(connector.notes)])
            html.extend(nested_html_table(rows))

            if connector.style != 'simple':
                pinhtml = []
                pinhtml.append('<table border="0" cellspacing="0" cellpadding="3" cellborder="1">')

                for pin, pinlabel in zip(connector.pins, connector.pinlabels):
                    if connector.hide_disconnected_pins and not connector.visible_pins.get(pin, False):
                        continue
                    pinhtml.append('   <tr>')
                    if connector.ports_left:
                        pinhtml.append(f'    <td port="p{pin}l">{pin}</td>')
                    if pinlabel:
                        pinhtml.append(f'    <td>{pinlabel}</td>')
                    if connector.ports_right:
                        pinhtml.append(f'    <td port="p{pin}r">{pin}</td>')
                    pinhtml.append('   </tr>')

                pinhtml.append('  </table>')

                html = [row.replace('<!-- connector table -->', '\n'.join(pinhtml)) for row in html]

            html = '\n'.join(html)
            dot.node(connector.name, label=f'<\n{html}\n>', shape='none', margin='0', style='filled', fillcolor='white')

            if len(connector.loops) > 0:
                dot.attr('edge', color='#000000:#ffffff:#000000')
                if connector.ports_left:
                    loop_side = 'l'
                    loop_dir = 'w'
                elif connector.ports_right:
                    loop_side = 'r'
                    loop_dir = 'e'
                else:
                    raise Exception('No side for loops')
                for loop in connector.loops:
                    dot.edge(f'{connector.name}:p{loop[0]}{loop_side}:{loop_dir}',
                             f'{connector.name}:p{loop[1]}{loop_side}:{loop_dir}')


        # determine if there are double- or triple-colored wires in the harness;
        # if so, pad single-color wires to make all wires of equal thickness
        pad = any(len(colorstr) > 2 for cable in self.cables.values() for colorstr in cable.colors)

        for cable in self.cables.values():

            html = []

            awg_fmt = ''
            if cable.show_equiv:
                # Only convert units we actually know about, i.e. currently
                # mm2 and awg --- other units _are_ technically allowed,
                # and passed through as-is.
                if cable.gauge_unit =='mm\u00B2':
                    awg_fmt = f' ({awg_equiv(cable.gauge)} AWG)'
                elif cable.gauge_unit.upper() == 'AWG':
                    awg_fmt = f' ({mm2_equiv(cable.gauge)} mm\u00B2)'

            rows = [[remove_links(cable.name) if cable.show_name else None],
                    [f'P/N: {remove_links(cable.pn)}' if (cable.pn and not isinstance(cable.pn, list)) else None,
                     html_line_breaks(manufacturer_info_field(
                        cable.manufacturer if not isinstance(cable.manufacturer, list) else None,
                        cable.mpn if not isinstance(cable.mpn, list) else None))],
                    [html_line_breaks(cable.type),
                     f'{cable.wirecount}x' if cable.show_wirecount else None,
                     f'{cable.gauge} {cable.gauge_unit}{awg_fmt}' if cable.gauge else None,
                     '+ S' if cable.shield else None,
                     f'{cable.length} m' if cable.length > 0 else None,
                     cable.color, html_colorbar(cable.color)],
                    '<!-- wire table -->',
                    [html_image(cable.image)],
                    [html_caption(cable.image)]]

            rows.extend(self.get_additional_component_table(cable))
            rows.append([html_line_breaks(cable.notes)])
            html.extend(nested_html_table(rows))

            wirehtml = []
            wirehtml.append('<table border="0" cellspacing="0" cellborder="0">')  # conductor table
            wirehtml.append('   <tr><td>&nbsp;</td></tr>')

            for i, connection_color in enumerate(cable.colors, 1):
                wirehtml.append('   <tr>')
                wirehtml.append(f'    <td><!-- {i}_in --></td>')
                wirehtml.append(f'    <td>{wv_colors.translate_color(connection_color, self.color_mode)}</td>')
                wirehtml.append(f'    <td><!-- {i}_out --></td>')
                wirehtml.append('   </tr>')

                bgcolors = ['#000000'] + get_color_hex(connection_color, pad=pad) + ['#000000']
                wirehtml.append(f'   <tr>')
                wirehtml.append(f'    <td colspan="3" border="0" cellspacing="0" cellpadding="0" port="w{i}" height="{(2 * len(bgcolors))}">')
                wirehtml.append('     <table cellspacing="0" cellborder="0" border="0">')
                for j, bgcolor in enumerate(bgcolors[::-1]):  # Reverse to match the curved wires when more than 2 colors
                    wirehtml.append(f'      <tr><td colspan="3" cellpadding="0" height="2" bgcolor="{bgcolor if bgcolor != "" else wv_colors.default_color}" border="0"></td></tr>')
                wirehtml.append('     </table>')
                wirehtml.append('    </td>')
                wirehtml.append('   </tr>')
                if cable.category == 'bundle':  # for bundles individual wires can have part information
                    # create a list of wire parameters
                    wireidentification = []
                    if isinstance(cable.pn, list):
                        wireidentification.append(f'P/N: {remove_links(cable.pn[i - 1])}')
                    manufacturer_info = manufacturer_info_field(
                        cable.manufacturer[i - 1] if isinstance(cable.manufacturer, list) else None,
                        cable.mpn[i - 1] if isinstance(cable.mpn, list) else None)
                    if manufacturer_info:
                        wireidentification.append(html_line_breaks(manufacturer_info))
                    # print parameters into a table row under the wire
                    if len(wireidentification) > 0 :
                        wirehtml.append('   <tr><td colspan="3">')
                        wirehtml.append('    <table border="0" cellspacing="0" cellborder="0"><tr>')
                        for attrib in wireidentification:
                            wirehtml.append(f'     <td>{attrib}</td>')
                        wirehtml.append('    </tr></table>')
                        wirehtml.append('   </td></tr>')

            if cable.shield:
                wirehtml.append('   <tr><td>&nbsp;</td></tr>')  # spacer
                wirehtml.append('   <tr>')
                wirehtml.append('    <td><!-- s_in --></td>')
                wirehtml.append('    <td>Shield</td>')
                wirehtml.append('    <td><!-- s_out --></td>')
                wirehtml.append('   </tr>')
                if isinstance(cable.shield, str):
                    # shield is shown with specified color and black borders
                    shield_color_hex = wv_colors.get_color_hex(cable.shield)[0]
                    attributes = f'height="6" bgcolor="{shield_color_hex}" border="2" sides="tb"'
                else:
                    # shield is shown as a thin black wire
                    attributes = f'height="2" bgcolor="#000000" border="0"'
                wirehtml.append(f'   <tr><td colspan="3" cellpadding="0" {attributes} port="ws"></td></tr>')

            wirehtml.append('   <tr><td>&nbsp;</td></tr>')
            wirehtml.append('  </table>')

            html = [row.replace('<!-- wire table -->', '\n'.join(wirehtml)) for row in html]

            # connections
            for connection_color in cable.connections:
                if isinstance(connection_color.via_port, int):  # check if it's an actual wire and not a shield
                    dot.attr('edge', color=':'.join(['#000000'] + wv_colors.get_color_hex(cable.colors[connection_color.via_port - 1], pad=pad) + ['#000000']))
                else:  # it's a shield connection
                    # shield is shown with specified color and black borders, or as a thin black wire otherwise
                    dot.attr('edge', color=':'.join(['#000000', shield_color_hex, '#000000']) if isinstance(cable.shield, str) else '#000000')
                if connection_color.from_port is not None:  # connect to left
                    from_port = f':p{connection_color.from_port}r' if self.connectors[connection_color.from_name].style != 'simple' else ''
                    code_left_1 = f'{connection_color.from_name}{from_port}:e'
                    code_left_2 = f'{cable.name}:w{connection_color.via_port}:w'
                    dot.edge(code_left_1, code_left_2)
                    from_string = f'{connection_color.from_name}:{connection_color.from_port}' if self.connectors[connection_color.from_name].show_name else ''
                    html = [row.replace(f'<!-- {connection_color.via_port}_in -->', from_string) for row in html]
                if connection_color.to_port is not None:  # connect to right
                    code_right_1 = f'{cable.name}:w{connection_color.via_port}:e'
                    to_port = f':p{connection_color.to_port}l' if self.connectors[connection_color.to_name].style != 'simple' else ''
                    code_right_2 = f'{connection_color.to_name}{to_port}:w'
                    dot.edge(code_right_1, code_right_2)
                    to_string = f'{connection_color.to_name}:{connection_color.to_port}' if self.connectors[connection_color.to_name].show_name else ''
                    html = [row.replace(f'<!-- {connection_color.via_port}_out -->', to_string) for row in html]

            html = '\n'.join(html)
            dot.node(cable.name, label=f'<\n{html}\n>', shape='box',
                     style='filled,dashed' if cable.category == 'bundle' else '', margin='0', fillcolor='white')

        for mate in self.mates:
            if mate.shape[0] == '<' and mate.shape[-1] == '>':
                dir = 'both'
            elif mate.shape[0] == '<':
                dir = 'back'
            elif mate.shape[-1] == '>':
                dir = 'forward'
            else:
                dir = 'none'  # should not happen

            if isinstance(mate, MatePin):
                color = '#000000'
            elif isinstance(mate, MateComponent):
                # color = '#000000:#ffffff:#000000'  # GraphViz bug? 'back' and 'both' do not work with multicolor edges
                color = '#000000'
            else:
                raise Exception(f'{mate} is an unknown mate')

            dot.attr('edge', color=color, style='dashed', dir=dir)
            from_port = f':p{mate.from_port}r' if isinstance(mate, MatePin) and self.connectors[mate.from_name].style != 'simple' else ''
            code_from = f'{mate.from_name}{from_port}:e'
            to_port = f':p{mate.to_port}l' if isinstance(mate, MatePin) and self.connectors[mate.to_name].style != 'simple' else ''
            code_to = f'{mate.to_name}{to_port}:w'
            print(mate, '---', code_from, '---', code_to)
            dot.edge(code_from, code_to)

        return dot

    @property
    def png(self):
        from io import BytesIO
        graph = self.create_graph()
        data = BytesIO()
        data.write(graph.pipe(format='png'))
        data.seek(0)
        return data.read()

    @property
    def svg(self):
        from io import BytesIO
        graph = self.create_graph()
        data = BytesIO()
        data.write(graph.pipe(format='svg'))
        data.seek(0)
        return data.read()

    def output(self, filename: (str, Path), view: bool = False, cleanup: bool = True, fmt: tuple = ('pdf', )) -> None:
        # graphical output
        graph = self.create_graph()
        for f in fmt:
            graph.format = f
            graph.render(filename=filename, view=view, cleanup=cleanup)
        graph.save(filename=f'{filename}.gv')
        # bom output
        bom_list = self.bom_list()
        with open_file_write(f'{filename}.bom.tsv') as file:
            file.write(tuplelist2tsv(bom_list))
        # HTML output
        with open_file_write(f'{filename}.html') as file:
            file.write('<!DOCTYPE html>\n')
            file.write('<html lang="en"><head>\n')
            file.write(' <meta charset="UTF-8">\n')
            file.write(f' <meta name="generator" content="{APP_NAME} {__version__} - {APP_URL}">\n')
            file.write(f' <title>{APP_NAME} Diagram and BOM</title>\n')
            file.write('</head><body style="font-family:Arial">\n')

            file.write('<h1>Diagram</h1>')
            with open_file_read(f'{filename}.svg') as svg:
                file.write(re.sub(
                    '^<[?]xml [^?>]*[?]>[^<]*<!DOCTYPE [^>]*>',
                    '<!-- XML and DOCTYPE declarations from SVG file removed -->',
                    svg.read(1024), 1))
                for svgdata in svg:
                    file.write(svgdata)

            file.write('<h1>Bill of Materials</h1>')
            listy = flatten2d(bom_list)
            file.write('<table style="border:1px solid #000000; font-size: 14pt; border-spacing: 0px">')
            file.write('<tr>')
            for item in listy[0]:
                file.write(f'<th style="text-align:left; border:1px solid #000000; padding: 8px">{item}</th>')
            file.write('</tr>')
            for row in listy[1:]:
                file.write('<tr>')
                for i, item in enumerate(row):
                    item_str = item.replace('\u00b2', '&sup2;')
                    align = 'text-align:right; ' if listy[0][i] == 'Qty' else ''
                    file.write(f'<td style="{align}border:1px solid #000000; padding: 4px">{item_str}</td>')
                file.write('</tr>')
            file.write('</table>')

            file.write('</body></html>')

    def get_additional_component_table(self, component: Union[Connector, Cable]) -> List[str]:
        rows = []
        if component.additional_components:
            rows.append(["Additional components"])
            for extra in component.additional_components:
                qty = extra.qty * component.get_qty_multiplier(extra.qty_multiplier)
                if self.mini_bom_mode:
                    id = self.get_bom_index(extra.description, extra.unit, extra.manufacturer, extra.mpn, extra.pn)
                    rows.append(component_table_entry(f'#{id} ({extra.type.rstrip()})', qty, extra.unit))
                else:
                    rows.append(component_table_entry(extra.description, qty, extra.unit, extra.pn, extra.manufacturer, extra.mpn))
        return(rows)

    def get_additional_component_bom(self, component: Union[Connector, Cable]) -> List[dict]:
        bom_entries = []
        for part in component.additional_components:
            qty = part.qty * component.get_qty_multiplier(part.qty_multiplier)
            bom_entries.append({
                'item': part.description,
                'qty': qty,
                'unit': part.unit,
                'manufacturer': part.manufacturer,
                'mpn': part.mpn,
                'pn': part.pn,
                'designators': component.name if component.show_name else None
            })
        return(bom_entries)

    def bom(self):
        # if the bom has previously been generated then return the generated bom
        if self._bom:
            return self._bom
        bom_entries = []

        # connectors
        for connector in self.connectors.values():
            if not connector.ignore_in_bom:
                description = ('Connector'
                               + (f', {connector.type}' if connector.type else '')
                               + (f', {connector.subtype}' if connector.subtype else '')
                               + (f', {connector.pincount} pins' if connector.show_pincount else '')
                               + (f', {connector.color}' if connector.color else ''))
                bom_entries.append({
                    'item': description, 'qty': 1, 'unit': None, 'designators': connector.name if connector.show_name else None,
                    'manufacturer': connector.manufacturer, 'mpn': connector.mpn, 'pn': connector.pn
                })

            # add connectors aditional components to bom
            bom_entries.extend(self.get_additional_component_bom(connector))

        # cables
        # TODO: If category can have other non-empty values than 'bundle', maybe it should be part of item name?
        for cable in self.cables.values():
            if not cable.ignore_in_bom:
                if cable.category != 'bundle':
                    # process cable as a single entity
                    description = ('Cable'
                                   + (f', {cable.type}' if cable.type else '')
                                   + (f', {cable.wirecount}')
                                   + (f' x {cable.gauge} {cable.gauge_unit}' if cable.gauge else ' wires')
                                   + (' shielded' if cable.shield else ''))
                    bom_entries.append({
                        'item': description, 'qty': cable.length, 'unit': 'm', 'designators': cable.name if cable.show_name else None,
                        'manufacturer': cable.manufacturer, 'mpn': cable.mpn, 'pn': cable.pn
                    })
                else:
                    # add each wire from the bundle to the bom
                    for index, color in enumerate(cable.colors):
                        description = ('Wire'
                                       + (f', {cable.type}' if cable.type else '')
                                       + (f', {cable.gauge} {cable.gauge_unit}' if cable.gauge else '')
                                       + (f', {color}' if color else ''))
                        bom_entries.append({
                            'item': description, 'qty': cable.length, 'unit': 'm', 'designators': cable.name if cable.show_name else None,
                            'manufacturer': index_if_list(cable.manufacturer, index),
                            'mpn': index_if_list(cable.mpn, index), 'pn': index_if_list(cable.pn, index)
                        })

            # add cable/bundles aditional components to bom
            bom_entries.extend(self.get_additional_component_bom(cable))

        for item in self.additional_bom_items:
            bom_entries.append({
                'item': item.get('description', ''), 'qty': item.get('qty', 1), 'unit': item.get('unit'), 'designators': item.get('designators'),
                'manufacturer': item.get('manufacturer'), 'mpn': item.get('mpn'), 'pn': item.get('pn')
            })

        # remove line breaks if present and cleanup any resulting whitespace issues
        bom_entries = [{k: clean_whitespace(v) for k, v in entry.items()} for entry in bom_entries]

        # deduplicate bom
        bom_types_group = lambda bt: (bt['item'], bt['unit'], bt['manufacturer'], bt['mpn'], bt['pn'])
        for group in Counter([bom_types_group(v) for v in bom_entries]):
            group_entries = [v for v in bom_entries if bom_types_group(v) == group]
            designators = []
            for group_entry in group_entries:
                if group_entry.get('designators'):
                    if isinstance(group_entry['designators'], List):
                        designators.extend(group_entry['designators'])
                    else:
                        designators.append(group_entry['designators'])
            designators = list(dict.fromkeys(designators))  # remove duplicates
            designators.sort()
            total_qty = sum(entry['qty'] for entry in group_entries)
            self._bom.append({**group_entries[0], 'qty': round(total_qty, 3), 'designators': designators})

        self._bom = sorted(self._bom, key=lambda k: k['item'])  # sort list of dicts by their values (https://stackoverflow.com/a/73050)

        # add an incrementing id to each bom item
        self._bom = [{**entry, 'id': index} for index, entry in enumerate(self._bom, 1)]
        return self._bom

    def get_bom_index(self, item, unit, manufacturer, mpn, pn):
        # Remove linebreaks and clean whitespace of values in search
        target = tuple(clean_whitespace(v) for v in (item, unit, manufacturer, mpn, pn))
        for entry in self.bom():
            if (entry['item'], entry['unit'], entry['manufacturer'], entry['mpn'], entry['pn']) == target:
                return entry['id']
        return None

    def bom_list(self):
        bom = self.bom()
        keys = ['id', 'item', 'qty', 'unit', 'designators'] # these BOM columns will always be included
        for fieldname in ['pn', 'manufacturer', 'mpn']: # these optional BOM columns will only be included if at least one BOM item actually uses them
            if any(entry.get(fieldname) for entry in bom):
                keys.append(fieldname)
        bom_list = []
        # list of staic bom header names,  headers not specified here are generated by capitilising the internal name
        bom_headings = {
            "pn": "P/N",
            "mpn": "MPN"
        }
        bom_list.append([(bom_headings[k] if k in bom_headings else k.capitalize()) for k in keys])  # create header row with keys
        for item in bom:
            item_list = [item.get(key, '') for key in keys]  # fill missing values with blanks
            item_list = [', '.join(subitem) if isinstance(subitem, List) else subitem for subitem in item_list]  # convert any lists into comma separated strings
            item_list = ['' if subitem is None else subitem for subitem in item_list]  # if a field is missing for some (but not all) BOM items
            bom_list.append(item_list)
        return bom_list
