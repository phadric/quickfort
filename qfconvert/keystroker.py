"""Handles conversion from QF keycode lists to keystrokes or DF macros."""

from math import sqrt
import os
import re
import random

from geometry import Area, Direction
import exetest
import util

# Keycode translation definitions for each output mode.
KEY_LIST = {
    'key': {
        '>': '^5',
        '<': '+5',
        '[n]': '8',
        '[ne]': '9',
        '[e]': '6',
        '[se]': '3',
        '[s]': '2',
        '[sw]': '1',
        '[w]': '4',
        '[nw]': '7',
        '[+n]': '+8',
        '[+ne]': '+9',
        '[+e]': '+6',
        '[+se]': '+3',
        '[+s]': '+2',
        '[+sw]': '+1',
        '[+w]': '+4',
        '[+nw]': '+7',
        '[widen]': 'k',
        '[heighten]': 'u',
        '[menudown]': '{NumpadAdd}', # move to next menu item
        '[menuup]': '{NumpadSub}',
        '!': '{Enter}', # select
        '#': '+{Enter}', # shift-select (select all)
        '%': '{wait}', # pause
        '^': '{Esc}' # exit current menu
        },
    'macro': {
        '>': '>',
        '<': '<',
        '[n]': '0:8',
        '[ne]': '0:9',
        '[e]': '0:6',
        '[se]': '0:3',
        '[s]': '0:2',
        '[sw]': '0:1',
        '[w]': '0:4',
        '[nw]': '0:7',
        '[+n]': '1:8',
        '[+ne]': '1:9',
        '[+e]': '1:6',
        '[+se]': '1:3',
        '[+s]': '1:2',
        '[+sw]': '1:1',
        '[+w]': '1:4',
        '[+nw]': '1:7',
        '[widen]': 'k',
        '[heighten]': 'u',
        '[menudown]': '+',
        '[menuup]': '-',
        '!': '0:Enter',
        '#': '1:Enter',
        '%': ''
    }
}


class Keystroker:
    """
    Computes keycodes needed to go through route and transforms those keycodes
    into keystrokes or DF macro commands.
    Returns list keystrokes or DF macro lines.
    """

    def __init__(self, grid, buildconfig):
        self.grid = grid
        self.buildconfig = buildconfig

    def plot(self, plots, cursor):
        """
        Plots a track through the grid following the positions in plots.
        Returns list of keycodes generated.
        """

        submenukeys = self.buildconfig.get('submenukeys')
        last_command = ''
        last_submenu = ''
        keys = self.buildconfig.get('init') or []

        # construct the list of keystrokes required to move to each
        # successive area and build it
        for pos in plots:
            cell = self.grid.get_cell(pos)
            command = cell.command
            endpos = cell.area.opposite_corner(pos)
            subs = {}

            # get samecmd or diffcmd depending on if command changed
            if command == last_command:
                nextcmd = self.buildconfig.get('samecmd', command) or []
            else:
                nextcmd = self.buildconfig.get('diffcmd', command) or []
                last_command = command

            # moveto = keys to move cursor to starting area-corner
            subs['moveto'] = self.move(cursor, pos)

            # setsize = keys to set area to desired dimensions
            setsizefun = getattr(self,
                self.buildconfig.get('setsize', command))
            setsize, newpos = setsizefun(pos, endpos)
            subs['setsize'] = setsize

            # setmats - keys to select mats for an area
            setmatscfg = self.buildconfig.get('setmats', command)
            if setmatscfg:
                setmatsfun = getattr(self, setmatscfg)
                subs['setmats'] = setmatsfun(cell.area.size())

            # handle submenus
            justcommand = None
            for k in submenukeys:
                if re.match(k, command):
                    # this command needs to be called in a DF submenu
                    submenu = command[0]

                    if not last_submenu:
                        # entering a new submenu and not currently in one
                        subs['menu'] = submenu
                        subs['exitmenu'] = []
                        last_submenu = submenu
                    elif last_submenu != submenu:
                        # switching from one submenu to another
                        subs['exitmenu'] = ['^'] # exit previous submenu
                        subs['menu'] = submenu # enter new menu
                        last_submenu = submenu
                    else:
                        # same submenu
                        subs['menu'] = []
                        subs['exitmenu'] = []

                    # drop the submenu key from command
                    justcommand = command[1:]
                    continue

            # no known submenu found in command?
            if not justcommand:
                if last_submenu:
                    # was in a submenu, now want to be at parent menu
                    subs['exitmenu'] = ['^']
                else:
                    # was at root menu and want to continue being there
                    subs['exitmenu'] = []

                subs['menu'] = []
                last_submenu = ''
                justcommand = command[:]

            # break command into keycodes
            codes = split_keystring_into_keycodes(justcommand)

            # substitute codes into nextcmd where we find string 'cmd'
            nextcodes = []
            for c in nextcmd:
                if c == 'cmd':
                    nextcodes.extend(codes)
                else:
                    nextcmd.append(c)

            # nextcodes is now our command-key string
            subs['cmd'] = nextcodes

            pattern = self.buildconfig.get('designate', command)

            newkeys = []
            # do pattern subs (and throw away empty elements)
            for p in pattern:
                if p in subs:
                    newkeys.extend(subs[p])
                else:
                    newkeys.append(p)

            # add our transformed keys to keys
            keys.extend(newkeys)

            # move cursor pos to end corner of built area
            cursor = newpos
        return keys

    def move(self, start, end, zoffset=0, allowjumps=True):
        """
        Returns list of keycodes to move DF cursor from Point start
        to Point end, as well as adjust z-level by zoffset if provided.
        """

        keys = []

        # do z-moves first if needed
        if zoffset > 0:
            keys.extend(['>'] * zoffset)
        elif zoffset < 0:
            keys.extend(['<'] * abs(zoffset))

        # while there are moves left to make..
        allow_backtrack = True
        while start != end:
            direction = Direction.get_direction(start, end)

            # Get x and y component of distance between start and end
            dx = abs(start.x - end.x)
            dy = abs(start.y - end.y)

            if dx == 0:
                steps = dy # moving on y axis only
            elif dy == 0:
                steps = dx # moving on x axis only
            else:
                # determine max diagonal steps we can take
                # in this direction without going too far
                steps = min([dx, dy])

            keycode = ['[' + direction.compass + ']']
            jumpkeycode = ['[+' + direction.compass + ']']
            move = direction.delta()
            if not allowjumps or steps < 8 or not allow_backtrack:
                # render single movement keys
                keys.extend(keycode * steps)
                start = start + (move * steps)
                allow_backtrack = True
            else:
                # use DF's move-by-10-units commands
                jumps = (steps // 10)
                leftover = steps % 10
                jumpmove = move * 10

                # backtracking optimization
                if leftover >= 8:
                    # test if jumping an extra 10-unit step
                    # would put us outside of the bounds of
                    # the blueprint (want to prevent)
                    test = start + (jumpmove * (jumps + 1))

                    if self.grid.is_out_of_bounds(test):
                        # just move there normally
                        keys.extend(keycode * leftover)
                        start = start + (move * steps)
                        # don't try to do this next iteration
                        allow_backtrack = False
                    else:
                        # permit overjump/backtracking movement
                        jumps += 1
                        start = start + (jumpmove * jumps)
                        allow_backtrack = True
                else:
                    # move the last few cells needed when using
                    # jumpmoves to land on the right spot
                    keys.extend(keycode * leftover)
                    # keys.append('%')
                    start = start + (move * steps)
                    allow_backtrack = True

                if jumps > 0:
                    keys.extend(jumpkeycode * jumps)

        return keys

    def setsize_standard(self, start, end):
        """
        Standard sizing mechanism for dig, place, query buildtypes.
        Returns keys, newpos:
            keys needed to make the currently-designating area the correct size
            pos is where the cursor ends up after sizing the area
        """
        return self.move(start, end), end

    def setsize_build(self, start, end):
        """
        Standard sizing mechanism for the build buildtype.
        Returns keys, pos:
            keys needed to make the currently-designating area the correct size
            pos is where the cursor ends up after sizing the area
        """
        # move cursor halfway to end from start
        midpoint = start.midpoint(end)
        keys = self.move(start, midpoint)

        # resize construction
        area = Area(start, end)
        keys += ['[widen]'] * (area.width() - 1)
        keys += ['[heighten]'] * (area.height() - 1)

        return keys, midpoint

    def setsize_fixed(self, start, end):
        """
        Sizing mechanism for fixed size buildings like 3x3 workshops,
        5x5 trade depots and 5x5 siege workshops. Here we just move to
        the center of the building and deploy it. This allows for e.g.
        a 3x3 grid of 'wc' cells indicating a single carpenter's workshop.
        Returns keys, pos:
            keys needed to make the currently-designating area the correct size
            pos is where the cursor ends up after sizing the area
        """
        # move cursor halfway to end from start
        midpoint = start.midpoint(end)
        keys = self.move(start, midpoint)

        return keys, midpoint

    def setmats(self, areasize):
        """
        Tries to avoid running out of a given material type by blithely
        attempting to all-select from DF's materials list repeatedly.
        qfconvert will attempt this 1+sqrt(areasize) times, which should
        be good enough most of the time.
        """
        if areasize == 1:
            return ['#']

        reps = 2 * int(sqrt(areasize))
        keys = ['#', '[menudown]'] * (reps - 1)
        keys.append('#')
        return keys



def convert_keys(keys, mode, title):
    """
    Convert keycodes to keystrokes or DF macro syntax based on mode.
    Returns string of all keystrokes or macro-content.
    """
    keys = translate_keycodes(keys, mode)
    if mode == 'macro':
        return '\n'.join(convert_to_macro(keys, title)) + '\n'
    elif mode == 'key':
        return ''.join(keys)
    else:
        raise Exception, 'Unknown Keystroker.render() mode "%s"' % mode


def translate_keycodes(keycodes, mode):
    """Translate keycodes based on given output mode."""
    return util.flatten( [ translate_keycode(k, mode) for k in keycodes ] )


def translate_keycode(keycode, mode):
    """
    Translate a given keycode against KEY_LIST and specified mode.
    Returns translation if one exists, or original keycode otherwise.
    """
    return KEY_LIST[mode].get(keycode) or keycode


def convert_to_macro(keycodes, title):
    """Convert keycodes to DF macro syntax (complete macro file contents)."""
    keybinds = parse_interface_txt(
        os.path.join(exetest.get_main_dir(), 'interface.txt') )

    if not title:
        title = '@@@qf' + str(random.randrange(0, 999999999))

    output = [title] # first line of macro is macro title

    for key in keycodes:
        if keybinds.get(key) is None:
            raise Exception, \
                "Key '%s' not bound in interface.txt" % key
        if key == '^':
            output.append('\t\tLEAVESCREEN') # escape menu key
        else:
            output.extend(keybinds[key])
        output.append('\tEnd of group')
    output.append('End of macro')

    return output


def split_keystring_into_keycodes(keystring):
    """
    Breaks str into individual keycodes.
    Returns a list of keycode strings.
    """

    # prepare to break keystring into keycodes
    cmdedit = re.sub(r'\{', '|{', keystring)
    cmdedit = re.sub(r'\}', '}|', cmdedit)
    cmdedit = re.sub(r'\+\!', '|+!|', cmdedit)
    cmdedit = re.sub(r'\!', '|!|', cmdedit)
    cmdedit = re.sub(r'\^', '|^|', cmdedit)
    cmdsplit = re.split(r'\|+', cmdedit)

    # break into individual keycodes
    codes = []
    for k in cmdsplit:
        if k[0] in ('{', '!', '^', '+'):
            codes.append(k) # preserve whole key-combos
        else:
            codes.extend(k) # separate individual keystrokes

    return codes


def parse_interface_txt(path):
    """
    Parse DF-syntax interface.txt.
    Returns a dictionary with keycodes as keys, whose values are lists of
        DF macro commands bound to said keycode.
    """
    with open(path) as f:
        data = f.read()

    groups = [re.split('\n', kb) for kb in re.split(r'\[BIND:', data)]

    keybinds = KEY_LIST
    for kb in groups:
        if kb == ['']:
            continue

        bind = re.sub(r'(\w+):.+', r'\1', kb[0])
        keys = [re.sub(r'\[(KEY:|SYM:)(.+?)\]', r'\2', k)
            for k in kb[1:] ]

        for k in keys:
            if k == '':
                continue
            if keybinds.get(k) is None:
                keybinds[k] = []
            keybinds[k].append('\t\t' + bind)
    return keybinds
