#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  king_phisher/client/graphs.py
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are
#  met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following disclaimer
#    in the documentation and/or other materials provided with the
#    distribution.
#  * Neither the name of the project nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
#  A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#  OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#  SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#  LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import collections
import ipaddress
import string

from king_phisher import color
from king_phisher import its
from king_phisher import ua_parser
from king_phisher.client import gui_utilities
from king_phisher.constants import ColorHexCode
from king_phisher.constants import OSFamily

from gi.repository import Gtk
from smoke_zephyr.requirements import check_requirements
from smoke_zephyr.utilities import unique

try:
	import matplotlib
	matplotlib.rcParams['backend'] = 'GTK3Cairo'
	from matplotlib import dates
	from matplotlib import patches
	from matplotlib import pyplot
	from matplotlib.backends.backend_gtk3cairo import FigureCanvasGTK3Cairo as FigureCanvas
	from matplotlib.backends.backend_gtk3cairo import FigureManagerGTK3Cairo as FigureManager
	from matplotlib.backends.backend_gtk3 import NavigationToolbar2GTK3 as NavigationToolbar
except ImportError:
	has_matplotlib = False
	"""Whether the :py:mod:`matplotlib` module is available."""
else:
	if not its.frozen and check_requirements(['matplotlib>=1.4.3']):
		has_matplotlib = False
	else:
		has_matplotlib = True

try:
	import mpl_toolkits.basemap
except ImportError:
	has_matplotlib_basemap = False
	"""Whether the :py:mod:`mpl_toolkits.basemap` module is available."""
else:
	if not its.frozen and check_requirements(['basemap>=1.0.7']):
		has_matplotlib_basemap = False
	else:
		has_matplotlib_basemap = True

EXPORTED_GRAPHS = {}

MPL_COLOR_LAND = 'gray'
MPL_COLOR_NULL = 'darkcyan'
MPL_COLOR_WATER = 'paleturquoise'

__all__ = ('export_graph_provider', 'get_graph', 'get_graphs', 'CampaignGraph')

def export_graph_provider(cls):
	"""
	Decorator to mark classes as valid graph providers. This decorator also sets
	the :py:attr:`~.CampaignGraph.name` attribute.

	:param class cls: The class to mark as a graph provider.
	:return: The *cls* parameter is returned.
	"""
	if not issubclass(cls, CampaignGraph):
		raise RuntimeError("{0} is not a subclass of CampaignGraph".format(cls.__name__))
	if not cls.is_available:
		return None
	graph_name = cls.__name__[13:]
	cls.name = graph_name
	EXPORTED_GRAPHS[graph_name] = cls
	return cls

def get_graph(graph_name):
	"""
	Return the graph providing class for *graph_name*. The class providing the
	specified graph must have been previously exported using
	:py:func:`.export_graph_provider`.

	:param str graph_name: The name of the graph provider.
	:return: The graph provider class.
	:rtype: :py:class:`.CampaignGraph`
	"""
	return EXPORTED_GRAPHS.get(graph_name)

def get_graphs():
	"""
	Get a list of all registered graph providers.

	:return: All registered graph providers.
	:rtype: list
	"""
	return sorted(EXPORTED_GRAPHS.keys())

class CampaignGraph(object):
	"""
	A basic graph provider for using :py:mod:`matplotlib` to create graph
	representations of campaign data. This class is meant to be subclassed
	by real providers.
	"""
	name = 'Unknown'
	"""The name of the graph provider."""
	name_human = 'Unknown'
	"""The human readable name of the graph provider used for UI identification."""
	graph_title = 'Unknown'
	"""The title that will be given to the graph."""
	table_subscriptions = []
	"""A list of tables from which information is needed to produce the graph."""
	is_available = True
	def __init__(self, application, size_request=None, style_context=None):
		"""
		:param tuple size_request: The size to set for the canvas.
		"""
		self.application = application
		self.style_context = style_context
		self.config = application.config
		"""A reference to the King Phisher client configuration."""
		self.figure, _ = pyplot.subplots()
		self.figure.set_facecolor(self.style_context_get_color('theme_color_graph_bg', default=ColorHexCode.WHITE))
		self.axes = self.figure.get_axes()
		self.canvas = FigureCanvas(self.figure)
		self.manager = None
		if size_request:
			self.canvas.set_size_request(*size_request)
		self.canvas.mpl_connect('button_press_event', self.mpl_signal_canvas_button_pressed)
		self.canvas.show()
		self.navigation_toolbar = NavigationToolbar(self.canvas, self.application.get_active_window())
		self.popup_menu = Gtk.Menu.new()

		menu_item = Gtk.MenuItem.new_with_label('Export')
		menu_item.connect('activate', self.signal_activate_popup_menu_export)
		self.popup_menu.append(menu_item)

		menu_item = Gtk.MenuItem.new_with_label('Refresh')
		menu_item.connect('activate', lambda action: self.refresh())
		self.popup_menu.append(menu_item)

		menu_item = Gtk.CheckMenuItem.new_with_label('Show Toolbar')
		menu_item.connect('toggled', self.signal_toggled_popup_menu_show_toolbar)
		self._menu_item_show_toolbar = menu_item
		self.popup_menu.append(menu_item)
		self.popup_menu.show_all()
		self.navigation_toolbar.hide()

	@property
	def rpc(self):
		return self.application.rpc

	@staticmethod
	def _ax_hide_ticks(ax):
		for tick in ax.yaxis.get_major_ticks():
			tick.tick1On = False
			tick.tick2On = False

	@staticmethod
	def _ax_set_spine_color(ax, spine_color):
		for pos in ('top', 'right', 'bottom', 'left'):
			ax.spines[pos].set_color(spine_color)

	def _load_graph(self, info_cache):
		raise NotImplementedError()

	def add_legend_patch(self, legend_rows, fontsize=None):
		handles = []
		if not fontsize:
			scale = self.markersize_scale
			if scale < 5:
				fontsize = 'xx-small'
			elif scale < 7:
				fontsize = 'x-small'
			elif scale < 9:
				fontsize = 'small'
			else:
				fontsize = 'medium'
		for row in legend_rows:
			handles.append(patches.Patch(color=row[0], label=row[1]))
		self.axes[0].legend(handles=handles, fontsize=fontsize, loc='lower right')

	def make_window(self):
		"""
		Create a window from the figure manager.

		:return: The graph in a new, dedicated window.
		:rtype: :py:class:`Gtk.Window`
		"""
		if self.manager is None:
			self.manager = FigureManager(self.canvas, 0)
		self.navigation_toolbar.destroy()
		self.navigation_toolbar = self.manager.toolbar
		self._menu_item_show_toolbar.set_active(True)
		window = self.manager.window
		window.set_transient_for(self.application.get_active_window())
		window.set_title(self.graph_title)
		return window

	@property
	def markersize_scale(self):
		bbox = self.axes[0].get_window_extent().transformed(self.figure.dpi_scale_trans.inverted())
		return max(bbox.width, bbox.width) * self.figure.dpi * 0.01

	def mpl_signal_canvas_button_pressed(self, event):
		if event.button != 3:
			return
		self.popup_menu.popup(None, None, None, None, event.button, Gtk.get_current_event_time())
		return True

	def signal_activate_popup_menu_export(self, action):
		dialog = gui_utilities.FileChooser('Export Graph', self.application.get_active_window())
		file_name = self.config['campaign_name'] + '.png'
		response = dialog.run_quick_save(file_name)
		dialog.destroy()
		if not response:
			return
		destination_file = response['target_path']
		self.figure.savefig(destination_file, format='png')

	def signal_toggled_popup_menu_show_toolbar(self, widget):
		if widget.get_property('active'):
			self.navigation_toolbar.show()
		else:
			self.navigation_toolbar.hide()

	def style_context_get_color(self, *args, **kwargs):
		sc_color = gui_utilities.gtk_style_context_get_color(self.style_context, *args, **kwargs)
		return (sc_color.red, sc_color.green, sc_color.blue)

	def load_graph(self):
		"""Load the graph information via :py:meth:`.refresh`."""
		self.refresh()

	def refresh(self, info_cache=None, stop_event=None):
		"""
		Refresh the graph data by retrieving the information from the
		remote server.

		:param dict info_cache: An optional cache of data tables.
		:param stop_event: An optional object indicating that the operation should stop.
		:type stop_event: :py:class:`threading.Event`
		:return: A dictionary of cached tables from the server.
		:rtype: dict
		"""
		info_cache = (info_cache or {})
		if not self.rpc:
			return info_cache
		for table in self.table_subscriptions:
			if stop_event and stop_event.is_set():
				return info_cache
			if not table in info_cache:
				info_cache[table] = tuple(self.rpc.remote_table(table, query_filter={'campaign_id': self.config['campaign_id']}))
		for ax in self.axes:
			ax.clear()
		self._load_graph(info_cache)
		self.figure.suptitle(
			self.graph_title,
			color=self.style_context_get_color('theme_color_graph_fg', default=ColorHexCode.BLACK),
			size=14,
			weight='bold',
			y=0.97
		)
		self.canvas.draw()
		return info_cache

class CampaignBarGraph(CampaignGraph):
	def __init__(self, *args, **kwargs):
		super(CampaignBarGraph, self).__init__(*args, **kwargs)
		self.figure.subplots_adjust(top=0.85, right=0.9, bottom=0.05, left=0.225)
		ax = self.axes[0]
		ax.tick_params(
			axis='both',
			top='off',
			right='off',
			bottom='off',
			left='off',
			labelbottom='off'
		)
		ax.invert_yaxis()
		self.axes.append(ax.twinx())

	def _barh(self, ax, bars, height, max_bars=None):
		# define the necessary colors
		color_bg = self.style_context_get_color('theme_color_graph_bg', default=ColorHexCode.WHITE)
		color_bar_bg = self.style_context_get_color('theme_color_graph_bar_bg', ColorHexCode.GRAY)
		color_bar_fg = self.style_context_get_color('theme_color_graph_bar_fg', ColorHexCode.BLACK)

		ax.set_axis_bgcolor(color_bg)

		# draw the foreground / filled bar
		bar_container = ax.barh(
			range(len(bars)),
			bars,
			height=height,
			color=color_bar_fg,
			linewidth=0
		)
		# draw the background / unfilled bar
		largest_bar = (max(bars) if len(bars) else 0)
		ax.barh(
			range(len(bars)),
			[largest_bar - bar for bar in bars],
			left=bars,
			height=height,
			color=color_bar_bg,
			linewidth=0
		)
		return bar_container

	def _load_graph(self, info_cache):
		raise NotImplementedError()

	def graph_bar(self, bars, max_bars, yticklabels, xlabel=None):
		"""
		Create a horizontal bar graph with better defaults for the standard use
		cases.

		:param list bars: The values of the bars to graph.
		:param int max_bars: The number to treat as the logical maximum number of plotted bars.
		:param list yticklabels: The labels to use on the x-axis.
		:param str xlabel: The label to give to the y-axis.
		:return: The bars created using :py:mod:`matplotlib`
		:rtype: `matplotlib.container.BarContainer`
		"""
		height = 0.25
		color_bg = self.style_context_get_color('theme_color_graph_bg', default=ColorHexCode.WHITE)
		color_fg = self.style_context_get_color('theme_color_graph_fg', default=ColorHexCode.BLACK)
		ax1, ax2 = self.axes  # primary axis
		bar_container = self._barh(ax1, bars, height, max_bars)

		yticks = [float(y) + (height / 2) for y in range(len(bars))]

		ax1.set_ybound(0, max(len(bars), max_bars))
		ax1.set_yticks(yticks)
		ax1.set_yticklabels(yticklabels, color=color_fg, size=10)

		ax2.set_yticks(yticks)
		ax2.set_yticklabels(["{0:,}".format(bar) for bar in bars], color=color_fg, size=12)
		ax2.set_ylim(ax1.get_ylim())

		# remove the y-axis tick marks
		self._ax_hide_ticks(ax1)
		self._ax_hide_ticks(ax2)
		self._ax_set_spine_color(ax1, color_bg)
		self._ax_set_spine_color(ax2, color_bg)

		if xlabel:
			ax1.set_xlabel(xlabel, color=color_fg, size=12)
		return bar_container

class CampaignLineGraph(CampaignGraph):
	def __init__(self, *args, **kwargs):
		super(CampaignLineGraph, self).__init__(*args, **kwargs)

	def _load_graph(self, info_cache):
		raise NotImplementedError()

class CampaignPieGraph(CampaignGraph):
	def __init__(self, *args, **kwargs):
		super(CampaignPieGraph, self).__init__(*args, **kwargs)

	def _load_graph(self, info_cache):
		raise NotImplementedError()

	def _graph_null_pie(self, title):
		ax = self.axes[0]
		ax.pie(
			(100,),
			autopct='%1.0f%%',
			colors=(self.style_context_get_color('theme_color_graph_pie_low', ColorHexCode.GRAY),),
			labels=(title,),
			shadow=True,
			startangle=90
		)
		ax.axis('equal')
		return

	def graph_pie(self, parts, autopct=None, labels=None, legend_labels=None):
		colors = color.get_scale(
			self.style_context_get_color('theme_color_graph_pie_high', ColorHexCode.BLACK),
			self.style_context_get_color('theme_color_graph_pie_low', ColorHexCode.GRAY),
			len(parts)
		)
		ax = self.axes[0]
		pie = ax.pie(
			parts,
			autopct=autopct,
			colors=colors,
			explode=[0.1] + ([0] * (len(parts) - 1)),
			labels=labels or tuple("{0:.1f}%".format(p) for p in parts),
			labeldistance=1.15,
			shadow=True,
			startangle=45,
			textprops={'color': self.style_context_get_color('theme_color_graph_fg', ColorHexCode.BLACK)},
			wedgeprops={'linewidth': 0}
		)
		ax.axis('equal')
		if legend_labels is not None:
			self.add_legend_patch(zip(colors, legend_labels), fontsize='x-small')
		return pie

@export_graph_provider
class CampaignGraphOverview(CampaignBarGraph):
	"""Display a graph which represents an overview of the campaign."""
	graph_title = 'Campaign Overview'
	name_human = 'Bar - Campaign Overview'
	table_subscriptions = ('credentials', 'visits')
	def _load_graph(self, info_cache):
		rpc = self.rpc
		visits = info_cache['visits']
		creds = info_cache['credentials']

		bars = []
		bars.append(rpc('db/table/count', 'messages', query_filter={'campaign_id': self.config['campaign_id']}))
		bars.append(len(visits))
		bars.append(len(unique(visits, key=lambda visit: visit.message_id)))
		if len(creds):
			bars.append(len(creds))
			bars.append(len(unique(creds, key=lambda cred: cred.message_id)))
		yticklabels = ('Messages', 'Visits', 'Unique\nVisits', 'Credentials', 'Unique\nCredentials')
		self.graph_bar(bars, len(yticklabels), yticklabels[:len(bars)])
		return

@export_graph_provider
class CampaignGraphVisitorInfo(CampaignBarGraph):
	"""Display a graph which shows the different operating systems seen from visitors."""
	graph_title = 'Campaign Visitor OS Information'
	name_human = 'Bar - Visitor OS Information'
	table_subscriptions = ('visits',)
	def _load_graph(self, info_cache):
		visits = info_cache['visits']

		operating_systems = collections.Counter()
		for visit in visits:
			ua = ua_parser.parse_user_agent(visit.visitor_details)
			operating_systems.update([ua.os_name or 'Unknown OS' if ua else 'Unknown OS'])

		os_names = operating_systems.keys()
		os_names.sort()
		bars = [operating_systems[os_name] for os_name in os_names]
		self.graph_bar(bars, len(OSFamily), os_names)
		return

@export_graph_provider
class CampaignGraphVisitorInfoPie(CampaignPieGraph):
	"""Display a graph which compares the different operating systems seen from visitors."""
	graph_title = 'Campaign Visitor OS Information'
	name_human = 'Pie - Visitor OS Information'
	table_subscriptions = ('visits',)
	def _load_graph(self, info_cache):
		visits = info_cache['visits']
		if not len(visits):
			self._graph_null_pie('No Visitor Information')
			return

		operating_systems = collections.Counter()
		for visit in visits:
			ua = ua_parser.parse_user_agent(visit.visitor_details)
			operating_systems.update([ua.os_name or 'Unknown OS' if ua else 'Unknown OS'])
		(os_names, count) = zip(*operating_systems.items())
		self.graph_pie(count, labels=tuple("{0:,}".format(os) for os in count), legend_labels=os_names)
		return

@export_graph_provider
class CampaignGraphVisitsTimeline(CampaignLineGraph):
	"""Display a graph which represents the visits of a campaign over time."""
	graph_title = 'Campaign Visits Timeline'
	name_human = 'Line - Visits Timeline'
	table_subscriptions = ('visits',)
	def _load_graph(self, info_cache):
		# define the necessary colors
		color_bg = self.style_context_get_color('theme_color_graph_bg', default=ColorHexCode.WHITE)
		color_fg = self.style_context_get_color('theme_color_graph_fg', default=ColorHexCode.BLACK)
		color_line_bg = self.style_context_get_color('theme_color_graph_line_bg', default=ColorHexCode.WHITE)
		color_line_fg = self.style_context_get_color('theme_color_graph_line_fg', default=ColorHexCode.BLACK)
		visits = info_cache['visits']
		first_visits = [visit.first_visit for visit in visits]

		ax = self.axes[0]
		ax.tick_params(
			axis='both',
			which='both',
			colors=color_fg,
			top='off',
			bottom='off'
		)
		ax.set_axis_bgcolor(color_line_bg)
		ax.set_ylabel('Number of Visits', color=self.style_context_get_color('theme_color_graph_fg', ColorHexCode.WHITE), size=10)
		self._ax_hide_ticks(ax)
		self._ax_set_spine_color(ax, color_bg)
		if not len(first_visits):
			ax.set_yticks((0,))
			ax.set_xticks((0,))
			return

		first_visits.sort()
		ax.plot_date(
			first_visits,
			range(1, len(first_visits) + 1),
			'-',
			color=color_line_fg,
			linewidth=6
		)
		self.figure.autofmt_xdate()
		self.figure.subplots_adjust(top=0.85, right=0.95, bottom=0.25, left=0.1)

		locator = dates.AutoDateLocator()
		ax.xaxis.set_major_locator(locator)
		ax.xaxis.set_major_formatter(dates.AutoDateFormatter(locator))
		return

@export_graph_provider
class CampaignGraphMessageResults(CampaignPieGraph):
	"""Display the percentage of messages which resulted in a visit."""
	graph_title = 'Campaign Message Results'
	name_human = 'Pie - Message Results'
	table_subscriptions = ('credentials', 'visits')
	def _load_graph(self, info_cache):
		rpc = self.rpc
		messages_count = rpc('db/table/count', 'messages', query_filter={'campaign_id': self.config['campaign_id']})
		if not messages_count:
			self._graph_null_pie('No Messages Sent')
			return
		visits_count = len(unique(info_cache['visits'], key=lambda visit: visit.message_id))
		credentials_count = len(unique(info_cache['credentials'], key=lambda cred: cred.message_id))

		assert credentials_count <= visits_count <= messages_count
		labels = ['Without Visit', 'With Visit', 'With Credentials']
		sizes = []
		sizes.append((float(messages_count - visits_count) / float(messages_count)) * 100)
		sizes.append((float(visits_count - credentials_count) / float(messages_count)) * 100)
		sizes.append((float(credentials_count) / float(messages_count)) * 100)
		if not credentials_count:
			labels.pop()
			sizes.pop()
		if not visits_count:
			labels.pop()
			sizes.pop()
		self.graph_pie(sizes, legend_labels=labels)
		return

class CampaignGraphVisitsMap(CampaignGraph):
	"""A base class to display a map which shows the locations of visit origins."""
	graph_title = 'Campaign Visit Locations'
	table_subscriptions = ('credentials', 'visits')
	is_available = has_matplotlib_basemap
	mpl_color_with_creds = 'indianred'
	mpl_color_without_creds = 'gold'
	draw_states = False
	def _load_graph(self, info_cache):
		visits = unique(info_cache['visits'], key=lambda visit: visit.message_id)
		cred_ips = set(cred.message_id for cred in info_cache['credentials'])
		cred_ips = set([visit.visitor_ip for visit in visits if visit.message_id in cred_ips])

		ax = self.axes[0]
		bm = mpl_toolkits.basemap.Basemap(resolution='c', ax=ax, **self.basemap_args)
		if self.draw_states:
			bm.drawstates()
		bm.drawcoastlines()
		bm.drawcountries()
		bm.fillcontinents(color=MPL_COLOR_LAND, lake_color=MPL_COLOR_WATER)
		bm.drawparallels((-60, -30, 0, 30, 60), labels=(1, 1, 0, 0))
		bm.drawmeridians((0, 90, 180, 270), labels=(0, 0, 0, 1))
		bm.drawmapboundary(fill_color=MPL_COLOR_WATER)

		if not visits:
			return

		ctr = collections.Counter()
		ctr.update([visit.visitor_ip for visit in visits])

		base_markersize = self.markersize_scale
		base_markersize = max(base_markersize, 3.05)
		base_markersize = min(base_markersize, 9)
		self._plot_visitor_map_points(bm, ctr, base_markersize, cred_ips)

		self.add_legend_patch(((self.mpl_color_with_creds, 'With Credentials'), (self.mpl_color_without_creds, 'Without Credentials')))
		return

	def _plot_visitor_map_points(self, bm, ctr, base_markersize, cred_ips):
		o_high = float(max(ctr.values()))
		o_low = float(min(ctr.values()))
		for visitor_ip, occurrences in ctr.items():
			visitor_ip = ipaddress.ip_address(visitor_ip)
			if visitor_ip.is_loopback or visitor_ip.is_private:
				continue
			geo_location = self.rpc.geoip_lookup(visitor_ip)
			if not geo_location:
				continue
			if not (geo_location.coordinates.longitude and geo_location.coordinates.latitude):
				continue
			pts = bm(geo_location.coordinates.longitude, geo_location.coordinates.latitude)
			if o_high == o_low:
				markersize = 2.0
			else:
				markersize = 1.0 + (float(occurrences) - o_low) / (o_high - o_low)
			markersize = markersize * base_markersize
			bm.plot(pts[0], pts[1], 'o', markerfacecolor=(self.mpl_color_with_creds if visitor_ip in cred_ips else self.mpl_color_without_creds), markersize=markersize)
		return

@export_graph_provider
class CampaignGraphVisitsMapUSA(CampaignGraphVisitsMap):
	"""Display a map of the USA which shows the locations of visit origins."""
	name_human = 'Map - Visit Locations (USA)'
	draw_states = True
	basemap_args = dict(projection='lcc', lat_1=30, lon_0=-90, llcrnrlon=-122.5, llcrnrlat=12.5, urcrnrlon=-45, urcrnrlat=50)

@export_graph_provider
class CampaignGraphVisitsMapWorld(CampaignGraphVisitsMap):
	"""Display a map of the world which shows the locations of visit origins."""
	name_human = 'Map - Visit Locations (World)'
	basemap_args = dict(projection='kav7', lon_0=0)

@export_graph_provider
class CampaignGraphPasswordComplexityPie(CampaignPieGraph):
	"""Display a graph which displays the number of passwords which meet standard complexity requirements."""
	graph_title = 'Campaign Password Complexity'
	name_human = 'Pie - Password Complexity'
	table_subscriptions = ('credentials',)
	def _load_graph(self, info_cache):
		passwords = set(cred.password for cred in info_cache['credentials'])
		if not len(passwords):
			self._graph_null_pie('No Credential Information')
			return
		ctr = collections.Counter()
		ctr.update(self._check_complexity(password) for password in passwords)

		self.graph_pie((ctr[True], ctr[False]), autopct='%1.1f%%', legend_labels=('Complex', 'Not Complex'))
		return

	def _check_complexity(self, password):
		if len(password) < 8:
			return False
		met = 0
		for char_set in (string.ascii_uppercase, string.ascii_lowercase, string.digits, string.punctuation):
			for char in password:
				if char in char_set:
					met += 1
					break
		return met >= 3
