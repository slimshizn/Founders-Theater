#!/usr/bin/env python3
"""Write the Browse windows: SiloBrowseRows.xml, SiloBrowseGrid.xml and SiloPerson.xml.

They share the Home look (wordmark lockup, hero, card rows, poster grid), so
the XML is generated here and committed. Control ids must match
resources/lib/silo/ui_browse.py.

Run: python3 scripts/skin_browse.py
"""

from pathlib import Path
from xml.dom import minidom

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources/skins/Default/1080i"

TEXT, SECONDARY, MUTED = "FFEDEDED", "BFEDEDED", "B3EDEDED"
CAPTION_IDLE, BLACK, SURFACE = "C7EDEDED", "FF000000", "FF15171C"

SLOTS, SLOT_BASE = 12, 3000
FOCUS_ZOOM = 'effect="zoom" start="100" end="{}" time="200" tween="cubic" easing="out" center="auto"'


def esc(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def rect(left, top, width, height):
    return "<left>{}</left><top>{}</top><width>{}</width><height>{}</height>".format(left, top, width, height)


def texture(name, tag="texture", color=None, border=None, diffuse=None, background=False):
    attrs = ""
    if color:
        attrs += ' colordiffuse="{}"'.format(color)
    if border:
        attrs += ' border="{}"'.format(border)
    if diffuse:
        attrs += ' diffuse="{}"'.format(diffuse)
    if background:
        attrs += ' background="true"'
    return "<{0}{1}>{2}</{0}>".format(tag, attrs, esc(name))


def visible(condition):
    return "<visible>{}</visible>".format(esc(condition)) if condition else ""


def prop(name):
    return "Window.Property({})".format(name)


def has(name):
    return "!String.IsEmpty({})".format(prop(name))



def aspect_ratio(aspect, diffuse=None):
    """A mask follows the control, not the cropped image: Kodi scales a diffuse
    texture with a "scale" image by default, cutting round photos flat at the
    top and bottom (or a poster's corners) whenever the image's shape differs."""
    if diffuse and aspect == "scale":
        return '<aspectratio scalediffuse="false">scale</aspectratio>'
    return "<aspectratio>{}</aspectratio>".format(aspect)

def image(left, top, width, height, name, color=None, border=None, diffuse=None, aspect=None,
          condition=None, control_id=None, fade=None, background=False):
    parts = ['<control type="image"{}>'.format(' id="{}"'.format(control_id) if control_id else ""),
             rect(left, top, width, height)]
    if aspect:
        parts.append(aspect if aspect.startswith("<") else aspect_ratio(aspect, diffuse))
    if fade:
        parts.append("<fadetime>{}</fadetime>".format(fade))
    parts += [texture(name, color=color, border=border, diffuse=diffuse, background=background),
              visible(condition), "</control>"]
    return "".join(parts)


def label(left, top, width, height, text, font, color, align="left", condition=None, control_id=None,
          scroll=False, kind="label", aligny=None):
    parts = ['<control type="{}"{}>'.format(kind, ' id="{}"'.format(control_id) if control_id else ""),
             rect(left, top, width, height), "<font>{}</font>".format(font),
             "<textcolor>{}</textcolor>".format(color), "<align>{}</align>".format(align)]
    parts.append("<aligny>{}</aligny>".format(aligny or ("bottom" if kind == "textbox" else "center")))
    if scroll:
        parts.append("<scroll>true</scroll>")
    parts += ["<label>{}</label>".format(esc(text)), visible(condition), "</control>"]
    return "".join(parts)


def nav(control_id, up=None, down=None, left=None, right=None):
    """Navigation tags; a missing direction points back at the control itself."""
    return "".join("<{0}>{1}</{0}>".format(tag, target or control_id)
                   for tag, target in (("onup", up), ("ondown", down), ("onleft", left), ("onright", right)))


def button(control_id, left, top, width, height, text, font="font25_title", color=TEXT, focus="pill56.png",
           focus_border=28, idle="pill56_idle.png", idle_border=28, align="center", offset=0, navigation="",
           condition=None):
    parts = ['<control type="button" id="{}">'.format(control_id), rect(left, top, width, height),
             "<label>{}</label>".format(esc(text)), "<font>{}</font>".format(font),
             "<textcolor>{}</textcolor><focusedcolor>{}</focusedcolor>".format(color, BLACK),
             "<align>{}</align><aligny>center</aligny>".format(align),
             "<textoffsetx>{}</textoffsetx>".format(offset) if offset else "",
             texture(focus, "texturefocus", color=TEXT, border=focus_border) if focus else "<texturefocus />",
             texture(idle, "texturenofocus", border=idle_border) if idle else "<texturenofocus />",
             navigation, visible(condition), "</control>"]
    return "".join(parts)


def group(control_id, left, top, width, height, children, condition=None, animation=""):
    head = '<control type="group"{}>'.format(' id="{}"'.format(control_id) if control_id else "")
    return "".join([head, rect(left, top, width, height), animation, visible(condition)] + children
                   + ["</control>"])


def progress(left, top, width):
    return "".join(['<control type="progress">', rect(left, top, width, 6),
                    "<info>ListItem.PercentPlayed</info>",
                    texture("white.png", "texturebg", color="33FFFFFF"),
                    texture("white.png", "midtexture", color=TEXT),
                    "<lefttexture /><righttexture /><overlaytexture />",
                    visible("!String.IsEmpty(ListItem.Property(silo.progress))"), "</control>"])


# ---------------------------------------------------------------- cards

def card_art(width, height, mask, art, wide, focused, list_id):
    """Card art, overlays and (focused) ring + glow, relative to the card's corner."""
    has_focus = "Control.HasFocus({})".format(list_id)
    parts = []
    if focused:
        parts.append(image(-24, -24, width + 48, height + 48, "glow16.png", border=48, condition=has_focus))
    parts += [image(0, 0, width, height, "round16.png", color=SURFACE, border=16),
              image(0, 0, width, height, "$INFO[ListItem.Art({})]".format(art), diffuse=mask, aspect="scale",
                    background=True)]
    if wide:
        parts += [image(0, 0, width, height, "scrim_bottom.png", color="D9000000", diffuse=mask),
                  image(14, height - 60, 124, 40, "round6.png", color="99000000", border=6,
                        condition="!String.IsEmpty(ListItem.Property(silo.badge))"),
                  label(14, height - 60, 124, 40, "$INFO[ListItem.Property(silo.badge)]", "font12", TEXT,
                        "center")]
    else:
        watched = "!String.IsEmpty(ListItem.Property(silo.watched))"
        parts += [image(width - 53, 11, 42, 42, "circle.png", color="FFFFFFFF", condition=watched),
                  image(width - 45, 19, 26, 26, "icon_check.png", color=BLACK, condition=watched)]
    parts.append(progress(16, height - 18, width - 32))
    if focused:
        parts.append(image(-4, -4, width + 8, height + 8, "ring16.png", color=TEXT, border=24,
                           condition=has_focus))
    return parts


def card_layout(tag, item_width, item_height, offset_x, offset_y, width, height, mask, art, wide, zoom, list_id,
                focused):
    has_focus = "Control.HasFocus({})".format(list_id)
    animation = ('<animation {} condition="{}">Conditional</animation>'.format(FOCUS_ZOOM.format(zoom), has_focus)
                 if focused else "")
    caption_top = offset_y + height + 14
    parts = ['<{} width="{}" height="{}">'.format(tag, item_width, item_height),
             group(None, offset_x, offset_y, width, height,
                   card_art(width, height, mask, art, wide, focused, list_id), animation=animation)]
    if focused:
        parts += [label(offset_x, caption_top, width, 40, "$INFO[ListItem.Label]", "font27", TEXT,
                        condition=has_focus, scroll=True),
                  label(offset_x, caption_top, width, 40, "$INFO[ListItem.Label]", "font27", CAPTION_IDLE,
                        condition="!" + has_focus)]
    else:
        parts.append(label(offset_x, caption_top, width, 40, "$INFO[ListItem.Label]", "font27", CAPTION_IDLE))
    parts += [label(offset_x, caption_top + 40, width, 34, "$INFO[ListItem.Label2]", "font12", MUTED),
              "</{}>".format(tag)]
    return "".join(parts)


def card_list(list_id, kind, left, top, width, height, item_width, item_height, offset_x, offset_y,
              card_width, card_height, mask, art, wide, zoom, navigation, orientation="horizontal"):
    layouts = [card_layout(tag, item_width, item_height, offset_x, offset_y, card_width, card_height, mask, art,
                           wide, zoom, list_id, tag == "focusedlayout")
               for tag in ("itemlayout", "focusedlayout")]
    extra = "<focusposition>0</focusposition>" if kind == "fixedlist" else ""
    return "".join(['<control type="{}" id="{}">'.format(kind, list_id), rect(left, top, width, height),
                    "<orientation>{}</orientation>".format(orientation), extra,
                    '<scrolltime tween="cubic" easing="out">200</scrolltime><preloaditems>2</preloaditems>',
                    navigation] + layouts + ["</control>"])


# ---------------------------------------------------------------- shared parts

def top_bar():
    """The FT icon on the left and the screen name as a selected tab in the middle."""
    return group(100, 0, 56, 1920, 64, [
        image(88, 4, 56, 56, "ft_icon.png", aspect="keep"),
        image(860, 2, 200, 60, "pill60_selected.png", border=30, control_id=102, condition=has("silo.title")),
        label(560, 2, 800, 60, "$INFO[{}]".format(prop("silo.title")), "font25_title", TEXT, "center",
              control_id=103),
        label(1560, 0, 272, 64, "$INFO[System.Time]", "font25_title", SECONDARY, "right"),
    ])


def hero():
    badges = []
    for slot in range(1, 5):
        name = "silo.hero.badge{}".format(slot)
        badges += [image(88, 346, 60, 36, "badge.png", border=8, control_id=410 + slot, condition=has(name)),
                   label(88, 346, 60, 36, "$INFO[{}]".format(prop(name)), "font_flag", TEXT, "center",
                         control_id=420 + slot)]
    logo = prop("silo.hero.logo")
    return group(400, 0, 0, 1920, 530, [
        image(691, 0, 1229, 756, "$INFO[{}]".format(prop("silo.hero.backdrop")), diffuse="mask_hero.png",
              aspect="scale", fade=400, background=True),
        image(0, 0, 1920, 240, "scrim_top.png", color=BLACK),
        group(401, 0, 0, 1920, 530, [
            image(88, 180, 880, 150, "$INFO[{}]".format(logo), fade=200, background=True,
                  aspect='<aspectratio align="left" aligny="bottom">keep</aspectratio>',
                  condition="!String.IsEmpty({})".format(logo)),
            label(88, 180, 880, 150, "$INFO[{}]".format(prop("silo.hero.title")), "font_MainMenu", TEXT,
                  condition="String.IsEmpty({})".format(logo), kind="textbox"),
        ] + badges + [
            label(88, 346, 880, 36, "$INFO[{}]".format(prop("silo.hero.meta")), "font12", SECONDARY,
                  control_id=430),
            label(88, 396, 780, 84, "$INFO[{}]".format(prop("silo.hero.synopsis")), "font27", MUTED,
                  kind="textbox", aligny="top"),
        ]),
    ], condition=has("silo.hero"))


def row_slot(index):
    base = SLOT_BASE + 10 * index
    wide_id, poster_id = base + 3, base + 4
    return group(base, 0, 530, 1920, 450, [
        image(88, 4, 44, 44, "icon_play_circle.png", control_id=base + 2),
        label(88, 0, 1600, 52, "", "font30_title", TEXT, control_id=base + 1),
        card_list(wide_id, "fixedlist", 52, 56, 1868, 330, 400, 330, 36, 32, 360, 202, "mask_wide.png",
                  "thumb", True, 104, nav(wide_id)),
        card_list(poster_id, "fixedlist", 48, 44, 1872, 406, 216, 406, 40, 44, 176, 264, "mask_poster.png",
                  "poster", False, 110, nav(poster_id)),
    ])


def empty_label(top=560):
    return label(88, top, 1744, 60, "$INFO[{}]".format(prop("silo.empty")), "font27", MUTED, "center",
                 control_id=550)


def document(controls, default):
    body = "".join([
        "<window>",
        '<defaultcontrol always="false">{}</defaultcontrol>'.format(default),
        "<backgroundcolor>FF000000</backgroundcolor>",
        "<coordinates><left>0</left><top>0</top></coordinates>",
        "<controls>",
        image(0, 0, 1920, 1080, "white.png", color=BLACK),
    ] + controls + ["</controls>", "</window>"])
    pretty = minidom.parseString(body).documentElement.toprettyxml(indent="  ")
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>',
                      "<!-- Generated by scripts/skin_browse.py; edit the generator, not this file. -->",
                      pretty.rstrip(), ""])


# ---------------------------------------------------------------- windows

def calendar_header():
    """Filter chip above a week control; Left/Right on the week control change the week."""
    return group(500, 0, 0, 1920, 360, [
        button(510, 88, 188, 320, 56, "", align="left", offset=28, navigation=nav(510)),
        image(356, 204, 24, 24, "icon_chevron_down.png", color=TEXT, control_id=512,
              condition="!Control.HasFocus(510)"),
        image(356, 204, 24, 24, "icon_chevron_down.png", color=BLACK, control_id=513,
              condition="Control.HasFocus(510)"),
        button(520, 88, 260, 520, 56, "", navigation=nav(520)),
        label(628, 260, 1000, 56, "$INFO[{}]".format(prop("silo.week_hint")), "font10", MUTED, control_id=521),
    ], condition=has("silo.header"))


def rows_window():
    return document([hero(), calendar_header()] + [row_slot(index) for index in range(SLOTS)]
                    + [empty_label(), top_bar()], SLOT_BASE + 4)


def grid_window():
    header = group(400, 0, 0, 1920, 420, [
        image(691, 0, 1229, 756, "$INFO[{}]".format(prop("silo.backdrop")), diffuse="mask_hero.png",
              aspect="scale", fade=400, background=True, condition=has("silo.backdrop")),
        image(0, 0, 1920, 1080, "white.png", color="99000000", condition=has("silo.backdrop")),
        image(0, 0, 1920, 240, "scrim_top.png", color=BLACK),
        label(88, 160, 1400, 72, "$INFO[{}]".format(prop("silo.heading")), "font52_title", TEXT, control_id=410),
        label(88, 232, 1400, 40, "$INFO[{}]".format(prop("silo.meta")), "font12", SECONDARY, control_id=420),
        label(88, 280, 1200, 110, "$INFO[{}]".format(prop("silo.overview")), "font27", MUTED, kind="textbox",
              aligny="top", control_id=430, condition=has("silo.overview")),
    ])
    grid = card_list(500, "panel", 40, 270, 1840, 810, 300, 538, 40, 40, 260, 390, "mask_grid.png", "poster",
                     False, 110, nav(500), orientation="vertical")
    return document([header, grid_chips(), grid, empty_label(640), top_bar()], 500)


def grid_chips():
    """A collection page's Sort, Filters, Clear filters and Shuffle chips (ui_browse.GRID_SORT…).
    Python sets their labels, widths, positions and left/right chain; Down reaches the grid."""
    shown = {510: None, 516: None, 517: has("silo.filter_count"), 518: has("silo.can_shuffle")}
    chips = [button(control_id, 88, 280, 240, 56, "", navigation=nav(control_id, down=500), condition=condition)
             for control_id, condition in shown.items()]
    return group(0, 0, 0, 1920, 1080, chips, condition=has("silo.controls"))


def person_window():
    photo = [image(0, 0, 260, 260, "circle.png", color=SURFACE),
             label(0, 0, 260, 260, "$INFO[{}]".format(prop("silo.initials")), "font52_title", MUTED, "center"),
             image(0, 0, 260, 260, "$INFO[{}]".format(prop("silo.photo")), diffuse="mask_circle.png",
                   aspect="scale", background=True, fade=200)]
    header = group(400, 0, 0, 1920, 530, [
        group(401, 88, 180, 260, 260, photo),
        label(400, 170, 1432, 72, "$INFO[{}]".format(prop("silo.name")), "font52_title", TEXT, control_id=410),
        label(400, 242, 1432, 40, "$INFO[{}]".format(prop("silo.facts")), "font12", SECONDARY, control_id=420),
        label(400, 292, 1432, 112, "$INFO[{}]".format(prop("silo.bio")), "font27", MUTED, kind="textbox",
              aligny="top", control_id=430),
        button(600, 400, 420, 240, 52, "Read more", font="font25_title", focus="pill52.png", focus_border=26,
               idle="pill52_idle.png", idle_border=26, navigation=nav(600), condition=has("silo.bio_more")),
    ])
    return document([header] + [row_slot(index) for index in range(SLOTS)] + [empty_label(), top_bar()],
                    SLOT_BASE + 4)


def first_visible(direction, targets):
    """Navigation to the first of ``targets`` that is visible, as skin_details does."""
    return "".join('<{0} condition="Control.IsVisible({1})">{1}</{0}>'.format(direction, target)
                   for target in targets)


def party_window():
    """The Watch Party lobby (ui_party.LobbyWindow): the title, the join code, who is here,
    and the buttons, Ready or Start (200), who may play and pause (202), Leave or End (201)."""
    backdrop = [
        image(691, 0, 1229, 756, "$INFO[{}]".format(prop("silo.backdrop")), diffuse="mask_hero.png",
              aspect="scale", fade=400, background=True, condition=has("silo.backdrop")),
        image(0, 0, 1920, 1080, "white.png", color="B3000000"),
    ]
    text = [
        label(88, 120, 1100, 40, "WATCH PARTY", "font12", SECONDARY),
        label(88, 160, 1100, 72, "$INFO[{}]".format(prop("silo.title")), "font52_title", TEXT, scroll=True),
        label(88, 232, 1100, 40, "$INFO[{}]".format(prop("silo.meta")), "font12", SECONDARY),
        label(88, 288, 1100, 44, "$INFO[{}]".format(prop("silo.status")), "font27", TEXT),
        label(1260, 120, 572, 40, "Join code", "font12", SECONDARY, "right"),
        label(1260, 160, 572, 72, "$INFO[{}]".format(prop("silo.code")), "font52_title", TEXT, "right"),
        label(88, 372, 900, 40, "$INFO[{}]".format(prop("silo.members_heading")), "font25_title", SECONDARY),
    ]

    def member(tag, focused):
        parts = ['<{} width="900" height="64">'.format(tag)]
        if focused:
            parts.append(image(0, 4, 900, 56, "white.png", color="1FFFFFFF", condition="Control.HasFocus(300)"))
        parts += [label(24, 0, 600, 64, "$INFO[ListItem.Label]", "font27", TEXT),
                  label(624, 0, 252, 64, "$INFO[ListItem.Label2]", "font12", SECONDARY, "right"),
                  "</{}>".format(tag)]
        return "".join(parts)

    members = ('<control type="list" id="300">' + rect(88, 420, 900, 576)
               + "<orientation>vertical</orientation><onup>300</onup><ondown>300</ondown><onleft>300</onleft>"
               + first_visible("onright", (200, 202, 201)) + member("itemlayout", False)
               + member("focusedlayout", True) + "</control>")
    buttons = [
        button(200, 1260, 420, 572, 64, "$INFO[{}]".format(prop("silo.primary_label")),
               navigation="<onup>200</onup>" + first_visible("ondown", (202, 201)) + "<onleft>300</onleft>"
                          "<onright>200</onright>", condition=has("silo.primary_label")),
        button(202, 1260, 500, 572, 64, "$INFO[{}]".format(prop("silo.policy_label")),
               navigation=first_visible("onup", (200,)) + "<onup>202</onup><ondown>201</ondown><onleft>300</onleft>"
                          "<onright>202</onright>", condition=has("silo.policy_label")),
        button(201, 1260, 580, 572, 64, "$INFO[{}]".format(prop("silo.leave_label")),
               navigation=first_visible("onup", (202, 200)) + "<onup>201</onup><ondown>201</ondown>"
                          "<onleft>300</onleft><onright>201</onright>"),
    ]
    return document(backdrop + text + [members] + buttons + [top_bar()], 200)


WINDOWS = {"SiloBrowseRows.xml": rows_window, "SiloBrowseGrid.xml": grid_window, "SiloPerson.xml": person_window,
           "SiloParty.xml": party_window}


def main():
    for name, build in WINDOWS.items():
        (OUT / name).write_text(build(), encoding="utf-8")
        print("wrote", (OUT / name).relative_to(ROOT))


if __name__ == "__main__":
    main()
