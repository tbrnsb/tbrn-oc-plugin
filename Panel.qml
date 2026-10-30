import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// TBRN OC Plugin — one bar robot, one panel, three tabs over the local
// OpenCode stats DB. Rendering is fed entirely by one JSON record that
// Main.qml's collector produces (see collect.py for the contract v2).
Panel {
  id: root
  moduleName: "tbrn.oc-plugin"
  ipcTarget: "tbrn.oc-plugin"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color track: Style.selectedFillFor(foreground, Color.accent)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string barGlyph: "󰉨"

  // The record, plus a revision hook so every derived value re-evaluates when
  // Main.qml swaps in a fresh record.
  readonly property var rec: usage.record
  readonly property int rev: usage.revision

  // 0 = Dashboard, 1 = Story, 2 = Overview
  property int tab: 0
  property bool cursorActive: false
  // Writable clock tick; the footer's "updated Ns ago" re-reads it while open.
  property int clock: 0
  // Range for TOKENS section: 0=7D, 1=1M, 2=1Y, 3=ALL
  property int range: 0

  readonly property var tabs: ["Dashboard", "Story", "Overview"]

  Main {
    id: usage
    settings: root.settings
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }
  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  function formatTokens(n) {
    var v = Number(n || 0)
    if (v >= 1e9) return (v / 1e9).toFixed(1) + "B"
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M"
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "K"
    return String(Math.round(v))
  }

  function formatMoney(c) {
    return "$" + (Number(c || 0)).toFixed(2)
  }

  function dayName(date) {
    var p = new Date(String(date || "") + "T00:00:00")
    if (isNaN(p.getTime())) return String(date || "")
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][p.getDay()]
  }

  function todayISO() {
    var n = new Date()
    return n.getFullYear() + "-" + String(n.getMonth() + 1).padStart(2, "0") + "-" + String(n.getDate()).padStart(2, "0")
  }

  // "updated Ns ago" from the record's generatedAt.
  function updatedAgo() {
    var r = usage.record
    if (!r || !r.generatedAt) return ""
    var then = new Date(r.generatedAt).getTime()
    if (isNaN(then)) return ""
    var secs = Math.max(0, Math.round((Date.now() - then) / 1000))
    if (secs < 60) return "updated " + secs + "s ago"
    if (secs < 3600) return "updated " + Math.round(secs / 60) + "m ago"
    return "updated " + Math.round(secs / 3600) + "h ago"
  }

  function heroMeta() {
    var r = usage.record
    if (!r) return ""
    var s = r.allTime ? r.allTime.sessions : 0
    var ago = updatedAgo()
    return formatTokens(s) + " sessions" + (ago ? " · " + ago : "")
  }

  function peakDay() {
    var r = usage.record
    if (!r || !r.daily) return 1
    var peak = 0
    for (var i = 0; i < r.daily.length; i++) peak = Math.max(peak, Number(r.daily[i].tokens || 0))
    return Math.max(1, peak)
  }

  // Token map for range totals (keys = "YYYY-MM-DD")
  function tokenMap() {
    var r = usage.record
    var m = {}
    if (!r || !r.daily) return m
    for (var i = 0; i < r.daily.length; i++) m[r.daily[i].date] = Number(r.daily[i].tokens || 0)
    return m
  }

  // Total tokens for a range: 0=7D, 1=1M, 2=1Y, 3=ALL
  function rangeTotal(rangeIdx) {
    var r = usage.record
    if (!r) return 0
    // Use OpenCode stats for 1Y and ALL to match OpenCode's /statistics
    if (rangeIdx >= 2 && r.stats && r.stats.oc_tokens) {
      var t = r.stats.oc_tokens
      return (t.input || 0) + (t.output || 0) + (t.reasoning || 0) + (t.cache?.read || 0) + (t.cache?.write || 0)
    }
    if (rangeIdx === 3) return Number(r.allTime.tokens || 0)
    var m = tokenMap()
    var today = new Date()
    var sum = 0
    var days = rangeIdx === 0 ? 7 : (rangeIdx === 1 ? 30 : 365)
    for (var d = 0; d < days; d++) {
      var date = new Date(today.getTime() - d * 86400000)
      var key = date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0")
      sum += m[key] || 0
    }
    return sum
  }

  // Week labels for 1M view
  function weekLabels() {
    var today = new Date()
    var labels = []
    for (var w = 3; w >= 0; w--) {
      var weekStart = new Date(today.getTime() - w * 7 * 86400000)
      var weekEnd = new Date(weekStart.getTime() + 6 * 86400000)
      var label = (w === 3 ? "1st" : w === 2 ? "2nd" : w === 1 ? "3rd" : "4th") + " week"
      labels.push(label)
    }
    return labels
  }

  function weekData() {
    var r = usage.record
    if (!r || !r.daily) return [{tokens:0,label:"1st week",formatted:"0"},{tokens:0,label:"2nd week",formatted:"0"},{tokens:0,label:"3rd week",formatted:"0"},{tokens:0,label:"4th week",formatted:"0"}]
    var m = tokenMap()
    var today = new Date()
    var weeks = []
    for (var w = 3; w >= 0; w--) {
      var sum = 0
      for (var d = 0; d < 7; d++) {
        var date = new Date(today.getTime() - (w * 7 + d) * 86400000)
        var key = date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0")
        sum += m[key] || 0
      }
      var label = (w === 3 ? "1st" : w === 2 ? "2nd" : w === 1 ? "3rd" : "4th") + " week"
      var formatted = sum >= 1e9 ? (sum / 1e9).toFixed(1) + "B" : (sum >= 1e6 ? (sum / 1e6).toFixed(1) + "M" : (sum >= 1e3 ? (sum / 1e3).toFixed(1) + "K" : String(Math.round(sum))))
      weeks.push({tokens: sum, label: label, formatted: formatted})
    }
    return weeks
  }

  function peakWeek() {
    var weeks = weekData()
    var peak = 0
    for (var i = 0; i < weeks.length; i++) peak = Math.max(peak, weeks[i])
    return Math.max(1, peak)
  }

  function selectTab(i) {
    tab = ((i % tabs.length) + tabs.length) % tabs.length
    cursorActive = true
    if (panelFlick) panelFlick.contentY = 0
  }

  function refreshNow() { usage.refreshAll() }

  function launchTerminal() {
    if (root.bar) root.bar.run("omarchy launch terminal")
    root.close()
  }

  // The bar slot must ALWAYS render its icon: the panel body handles the
  // empty case itself. (Root self-hide collapsed the bar slot permanently —
  // the original "invisible icon" bug.)
  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onTabChanged: if (panelFlick) panelFlick.contentY = 0
  onOpenedChanged: if (opened) {
    cursorActive = false
    if (panelFlick) panelFlick.contentY = 0
    usage.refreshAll()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Timer {
    interval: 30000
    running: root.opened
    repeat: true
    onTriggered: root.clock++
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refreshNow(); return "ok" }
    function next(): string { root.selectTab(root.tab + 1); return "ok" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    iconComponent: Component {
      Image {
        source: "assets/icons/robot-classic.svg"
        width: Style.bar.iconCanvas
        height: Style.bar.iconCanvas
        fillMode: Image.PreserveAspectFit
      }
    }
    active: root.tab !== 0
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.launchTerminal()
      else if (buttonCode === Qt.MiddleButton) root.selectTab(root.tab + 1)
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function(dx, dy) {
        if (dx !== 0) root.selectTab(root.tab + dx)
        if (dy !== 0)
          panelFlick.contentY = root.clamp(panelFlick.contentY + dy * Style.space(56), 0,
                                           Math.max(0, panelFlick.contentHeight - panelFlick.height))
      }
      onActivateRequested: root.refreshNow()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refreshNow()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // ---------- Hero ----------
          PanelHero {
            width: parent.width
            visible: !!root.rec
            title: "TBRN"
            meta: root.heroMeta()
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Image {
                source: "assets/icons/robot-classic.svg"
                width: Style.font.display * 1.6
                height: Style.font.display * 1.6
                fillMode: Image.PreserveAspectFit
              }
            }
          }

          Text {
            visible: !root.rec
            width: parent.width
            topPadding: Style.space(24)
            text: "No OpenCode sessions yet.\nThey show up here once you've run some. (v2)"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }

          // ---------- Tab strip ----------
          Row {
            id: tabStrip
            visible: !!root.rec
            width: parent.width
            spacing: Style.spacing.sm

            readonly property real cellWidth: (width - spacing * ((root.tabs ? root.tabs.length : 0) - 1)) / Math.max(1, (root.tabs ? root.tabs.length : 1))

            Repeater {
              model: ["Dashboard", "Story", "Overview"]
              Button {
                required property string modelData
                required property int index
                width: tabStrip.cellWidth
                text: modelData
                selected: index === root.tab
                hasCursor: root.cursorActive && index === root.tab
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.selectTab(index)
                onHovered: function(isHovered) { if (isHovered) root.cursorActive = true }
              }
            }
          }

          // ================= DASHBOARD =================
          Column {
            visible: root.tab === 0 && !!root.rec
            width: parent.width
            spacing: Style.spacing.md

            // ACTIVITY (replaces TODAY) - matches OpenCode /statistics
            PanelSectionHeader { width: parent.width; text: "ACTIVITY"; foreground: root.foreground; fontFamily: root.fontFamily }
            Row {
              width: parent.width
              spacing: 0
              Repeater {
                model: root.rec && root.rec.stats ? [
                  { label: "BEST STREAK", value: (root.rec.stats.bestStreak || 0) + "d" },
                  { label: "ACTIVE DAYS", value: String(root.rec.stats.activeDays || 0) },
                  { label: "SESSIONS", value: root.formatTokens(root.rec.stats.sessions || 0) },
                  { label: "SUBAGENTS", value: String(root.rec.stats.subagents || 0) },
                  { label: "PROMPTS", value: root.formatTokens(root.rec.stats.prompts || 0) },
                  { label: "STEPS", value: root.formatTokens(root.rec.stats.steps || 0) },
                  { label: "CURRENT", value: (root.rec.stats.currentStreak || 0) + "d" }
                ] : []
                Item {
                  required property var modelData
                  width: parent.width / 4
                  implicitHeight: tileCol.implicitHeight
                  Column {
                    id: tileCol
                    anchors.centerIn: parent
                    spacing: Style.space(2)
                    Text {
                      textFormat: Text.PlainText
                      text: modelData.label
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                      textFormat: Text.PlainText
                      text: modelData.value
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                      horizontalAlignment: Text.AlignHCenter
                    }
                  }
                }
              }
            }

            PanelSeparator { width: parent.width; foreground: root.foreground }

            // TOKENS with range toggle: header left (with range total), toggles right
            Item {
              width: parent.width
              implicitHeight: tokensToggleRow.implicitHeight
              PanelSectionHeader {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "TOKENS · " + root.formatTokens(root.rangeTotal(root.range))
                foreground: root.foreground
                fontFamily: root.fontFamily
              }
              Row {
                id: tokensToggleRow
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(4)
                Repeater {
                  model: ["7D", "1M", "1Y", "ALL"]
                  Button {
                    required property string modelData
                    required property int index
                    text: modelData
                    selected: index === root.range
                    bordered: true
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.caption
                    verticalPadding: Style.space(2)
                    onClicked: root.range = index
                  }
                }
              }
            }

            // 7D = DayRows, 1M = WeekRows, 1Y/ALL = GitHub-style Heatmap
            Item {
              width: parent.width
              height: root.range === 0 ? dayRows.implicitHeight : (root.range === 1 ? weekRows.implicitHeight : heatmap.implicitHeight)
              Column {
                id: dayRows
                visible: root.range === 0
                width: parent.width
                spacing: Style.spacing.xs
                Repeater {
                  model: root.rec ? root.rec.daily.slice(-7) : []
                  DayRow {
                    required property var modelData
                    width: parent.width
                    day: modelData
                    ratio: Number(modelData.tokens || 0) / root.peakDay()
                    today: String(modelData.date || "") === root.todayISO()
                  }
                }
              }
              Column {
                id: weekRows
                visible: root.range === 1
                width: parent.width
                spacing: Style.spacing.xs
                Repeater {
                  model: root.rec ? root.weekData() : [{tokens:0,label:"1st week",formatted:"0"},{tokens:0,label:"2nd week",formatted:"0"},{tokens:0,label:"3rd week",formatted:"0"},{tokens:0,label:"4th week",formatted:"0"}]
                  WeekRow {
                    required property var modelData
                    width: parent.width
                    tokens: modelData.tokens
                    ratio: Number(modelData.tokens || 0) / root.peakWeek()
                    label: modelData.label
                    formatted: modelData.formatted
                  }
                }
              }
              Heatmap {
                id: heatmap
                visible: root.range >= 2
                width: parent.width
                daily: root.rec ? root.rec.daily : []
                // range 2=1Y (52 weeks), 3=ALL (53 weeks)
                weeks: root.range === 2 ? 52 : 53
              }
            }

            PanelSeparator { width: parent.width; foreground: root.foreground }

            // TOKENS BY MODEL
            PanelSectionHeader { width: parent.width; text: "TOKENS BY MODEL"; foreground: root.foreground; fontFamily: root.fontFamily }
            CollapsibleRows {
              id: modelsCollapsible
              items: root.rec ? root.modelRows() : []
              preview: 5
            }
          }

          // ================= STORY =================
          Column {
            visible: root.tab === 1 && !!root.rec
            width: parent.width
            spacing: Style.spacing.md

            // PEAK HOURS (moved to top)
            PanelSectionHeader { width: parent.width; text: "PEAK HOURS"; foreground: root.foreground; fontFamily: root.fontFamily }
            HourBars { width: parent.width; hours: root.rec ? root.rec.hours : [] }

            PanelSeparator { width: parent.width; foreground: root.foreground }

            // AGENTS (top-level)
            PanelSectionHeader { width: parent.width; text: "AGENTS"; foreground: root.foreground; fontFamily: root.fontFamily }
            CollapsibleRows {
              id: agentsCollapsible
              items: root.rec ? root.agentRows() : []
              preview: 8
            }

            PanelSeparator { width: parent.width; foreground: root.foreground }

            // DISPATCH GRAPH
            PanelSectionHeader { width: parent.width; text: "DISPATCH"; foreground: root.foreground; fontFamily: root.fontFamily }
            Repeater {
              model: root.rec ? root.rec.dispatch : []
              DispatchRow {
                required property var modelData
                width: parent.width
                from: modelData.from
                to: modelData.to
                count: modelData.count
                share: Number(modelData.count || 0) / Math.max(1, Number((root.rec.dispatch[0] || {}).count || 1))
              }
            }

            PanelSeparator { width: parent.width; foreground: root.foreground }

            // SUBAGENTS
            PanelSectionHeader { width: parent.width; text: "SUBAGENTS"; foreground: root.foreground; fontFamily: root.fontFamily }
            CollapsibleRows {
              id: subagentsCollapsible
              items: root.rec ? root.subagentRows() : []
              preview: 8
            }
          }

          // ================= OVERVIEW =================
          Column {
            visible: root.tab === 2 && !!root.rec
            width: parent.width
            spacing: Style.spacing.md

            // ALL-TIME (matches OpenCode /statistics)
            PanelSectionHeader { width: parent.width; text: "ALL-TIME"; foreground: root.foreground; fontFamily: root.fontFamily }
            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.rec && root.rec.stats.oc_sessions ? (
                root.formatTokens(root.rec.stats.oc_tokens ? (root.rec.stats.oc_tokens.input + root.rec.stats.oc_tokens.output + root.rec.stats.oc_tokens.reasoning + root.rec.stats.oc_tokens.cache.read + root.rec.stats.oc_tokens.cache.write) : 0) + " tokens · " + root.rec.stats.oc_sessions + " sessions\n"
                + "subagents: " + root.rec.stats.oc_subagents + "\n"
                + "prompts: " + root.rec.stats.oc_prompts + " · steps: " + root.rec.stats.oc_steps + "\n"
                + root.formatMoney(root.rec.stats.oc_cost)
              ) : (root.rec ? (root.formatTokens(root.rec.allTime.tokens) + " tokens · " + root.rec.allTime.sessions + " sessions\n"
                                + "+" + root.rec.allTime.additions + " / -" + root.rec.allTime.deletions + " lines · "
                                + root.formatMoney(root.rec.allTime.cost)) : "")
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }

            // TOOLS with Other dropdown
            PanelSectionHeader { width: parent.width; text: "TOOLS"; foreground: root.foreground; fontFamily: root.fontFamily }
            CollapsibleRows {
              id: toolsCollapsible
              items: root.rec ? root.toolRows() : []
              preview: 8
            }

            PanelSeparator { width: parent.width; foreground: root.foreground }

            // PROVIDERS as collapsed dropdown (default closed)
            CollapsibleRows {
              id: providersCollapsible
              items: root.rec ? root.providerRows() : []
              preview: 0
              collapsedLabel: "Providers"
              expanded: false
            }
          }

        }
      }
    }
  }

  // ---------- helper functions for shaped arrays ----------

  function agentRows() {
    if (!rec || !rec.agents) return []
    var maxS = Math.max(1, Number(rec.agents[0]?.sessions || 1))
    return rec.agents.map(function(a) { return { label: a.name, valueText: a.sessions + "s", share: Number(a.sessions || 0) / maxS } })
  }

  function subagentRows() {
    if (!rec || !rec.subagents) return []
    var maxS = Math.max(1, Number(rec.subagents[0]?.sessions || 1))
    return rec.subagents.map(function(a) { return { label: a.name, valueText: a.sessions + "s", share: Number(a.sessions || 0) / maxS } })
  }

  function toolRows() {
    if (!rec || !rec.tools) return []
    var maxC = Math.max(1, Number(rec.tools[0]?.calls || 1))
    return rec.tools.map(function(t) { return { label: t.name, valueText: root.formatTokens(t.calls), share: Number(t.calls || 0) / maxC } })
  }

  function providerRows() {
    if (!rec || !rec.providers) return []
    var maxS = Math.max(1, Number(rec.providers[0]?.sessions || 1))
    return rec.providers.map(function(p) { return { label: p.name, valueText: p.sessions + "s", share: Number(p.sessions || 0) / maxS } })
  }

  function modelRows() {
    if (!rec || !rec.models) return []
    var maxT = Math.max(1, Number(rec.models[0]?.tokens || 1))
    return rec.models.map(function(m) { return { label: m.name, valueText: root.formatTokens(m.tokens), share: Number(m.tokens || 0) / maxT } })
  }

  // ---------- shared row components ----------

  // A rounded meter showing a fraction of an allowance.
  component Meter: Item {
    id: meter
    property real value: -1
    property bool alarming: false
    property real thickness: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))
    implicitHeight: thickness

    Rectangle {
      id: trackRect
      anchors.fill: parent
      radius: height / 2
      color: root.track
    }
    Rectangle {
      anchors.left: trackRect.left
      anchors.verticalCenter: trackRect.verticalCenter
      height: trackRect.height
      radius: trackRect.radius
      width: trackRect.width * root.clamp(meter.value, 0, 1)
      color: meter.alarming ? root.urgent : root.foreground
      Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    }
  }

  // One row per day: label, bar, tokens. Today is picked out in full foreground.
  component DayRow: Item {
    id: dayRow
    property var day: null
    property real ratio: 0
    property bool today: false
    implicitHeight: Math.max(dayLabel.implicitHeight, dayValue.implicitHeight) + Style.spacing.sm

    Text {
      id: dayLabel
      textFormat: Text.PlainText
      text: dayRow.today ? "Today" : root.dayName(dayRow.day ? dayRow.day.date : "")
      color: dayRow.today ? root.foreground : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: dayRow.today
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }
    Rectangle {
      id: dayTrack
      anchors.left: dayLabel.right
      anchors.right: dayValue.left
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      height: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))
      radius: height / 2
      color: root.track
      Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        height: parent.height
        radius: parent.radius
        width: parent.width * root.clamp(dayRow.ratio, 0, 1)
        color: dayRow.today ? root.foreground : root.alpha(root.foreground, 0.55)
        Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
      }
    }
    Text {
      id: dayValue
      textFormat: Text.PlainText
      text: root.formatTokens(dayRow.day ? dayRow.day.tokens : 0)
      color: dayRow.today ? root.foreground : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      horizontalAlignment: Text.AlignRight
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }
  }

  // Week row for 1M view: label (week), bar, tokens. Similar to DayRow.
  component WeekRow: Item {
    id: weekRow
    property int weekIndex: 0
    property int tokens: 0
    property real ratio: 0
    property string label: ""
    property string formatted: ""
    implicitHeight: Math.max(weekLabel.implicitHeight, weekValue.implicitHeight) + Style.spacing.sm

    Text {
      id: weekLabel
      textFormat: Text.PlainText
      text: weekRow.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }
    Rectangle {
      id: weekTrack
      anchors.left: weekLabel.right
      anchors.right: weekValue.left
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      height: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))
      radius: height / 2
      color: root.track
      Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        height: parent.height
        radius: parent.radius
        width: parent.width * root.clamp(weekRow.ratio, 0, 1)
        color: root.foreground
        Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
      }
    }
    Text {
      id: weekValue
      textFormat: Text.PlainText
      text: weekRow.formatted
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      horizontalAlignment: Text.AlignRight
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }
  }

  // A generic share-bar row: label + bar behind, value on the right.
  component ShareRow: Item {
    id: shareRow
    property string label: ""
    property string valueText: ""
    property real share: 0
    implicitHeight: labelText.implicitHeight + Style.spacing.lg

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: root.alpha(root.foreground, 0.05)
    }
    Rectangle {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: parent.width * root.clamp(shareRow.share, 0, 1)
      radius: Style.cornerRadius
      color: root.alpha(root.foreground, 0.14)
      Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    }
    Text {
      id: labelText
      textFormat: Text.PlainText
      text: shareRow.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.right: valueTextItem.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {
      id: valueTextItem
      textFormat: Text.PlainText
      text: shareRow.valueText
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }
  }

  // Dispatch row: dim "from", arrow, full-foreground "to", count on the right,
  // with a faint bar behind scaled to the top edge count.
  component DispatchRow: Item {
    id: dispatchRow
    property string from: ""
    property string to: ""
    property int count: 0
    property real share: 0
    implicitHeight: flowLabel.implicitHeight + Style.spacing.lg

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: root.alpha(root.foreground, 0.05)
    }
    Rectangle {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: parent.width * root.clamp(dispatchRow.share, 0, 1)
      radius: Style.cornerRadius
      color: root.alpha(root.foreground, 0.10)
    }
    Row {
      id: flowLabel
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.right: countText.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(4)
      Text {
        textFormat: Text.PlainText
        text: dispatchRow.from
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        elide: Text.ElideRight
        width: Math.min(implicitWidth, flowLabel.width * 0.4)
      }
      Text {
        text: "→"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      Text {
        textFormat: Text.PlainText
        text: dispatchRow.to
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
        elide: Text.ElideRight
      }
    }
    Text {
      id: countText
      textFormat: Text.PlainText
      text: dispatchRow.count + "×"
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }
  }

  // A 24-bar hour histogram. Peak hour is full foreground; the rest are dim.
  // Hour labels sit under 0 / 6 / 12 / 18.
  component HourBars: Item {
    id: hourBars
    property var hours: []
    readonly property int peak: {
      var m = 0
      for (var i = 0; i < hours.length; i++) m = Math.max(m, Number(hours[i] || 0))
      return Math.max(1, m)
    }
    implicitHeight: bars.implicitHeight + labelRow.implicitHeight + Style.space(4)

    Column {
      id: bars
      width: parent.width
      spacing: Style.space(4)
      Row {
        width: parent.width
        spacing: Style.space(2)
        Repeater {
          model: 24
          Rectangle {
            required property int index
            width: (bars.width - 23 * Style.space(2)) / 24
            height: Math.max(Style.space(3), Math.round(Style.space(40) * Number((hourBars.hours[index] || 0)) / hourBars.peak))
            radius: Style.space(2)
            color: (Number(hourBars.hours[index] || 0) === hourBars.peak)
              ? root.foreground : root.alpha(root.foreground, 0.35)
            anchors.bottom: parent.bottom
          }
        }
      }
    }
    Row {
      id: labelRow
      width: parent.width
      spacing: Style.space(2)
      Repeater {
        model: [0, 6, 12, 18]
        Text {
          required property int modelData
          textFormat: Text.PlainText
          text: String(modelData).padStart(2, "0")
          width: (bars.width - 23 * Style.space(2)) / 24 * 6
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignLeft
        }
      }
    }
  }

  // Collapsible rows: shows preview items + "Other (N)" toggle button.
  component CollapsibleRows: Column {
    id: collapsible
    property var items: []           // [{label, valueText, share}]
    property int preview: 8
    property string collapsedLabel: "Other"
    property bool expanded: false
    width: parent.width
    spacing: Style.space(4)

    Repeater {
      model: collapsible.expanded ? collapsible.items.length : Math.min(collapsible.preview, collapsible.items.length)
      delegate: ShareRow {
        required property int index
        width: parent.width
        label: collapsible.items[index].label
        valueText: collapsible.items[index].valueText
        share: collapsible.items[index].share
      }
    }
    Button {
      visible: collapsible.items.length > collapsible.preview
      width: parent.width
      text: collapsible.expanded ? "Show less" : (collapsible.collapsedLabel + " (" + (collapsible.items.length - collapsible.preview) + ")")
      bordered: true
      foreground: root.foreground
      fontFamily: root.fontFamily
      fontSize: Style.font.caption
      verticalPadding: Style.space(4)
      onClicked: collapsible.expanded = !collapsible.expanded
    }
  }

  // GitHub-style heatmap: weeks as columns, 7 rows = Mon..Sun.
  // weeks: number of week columns (5 for 1M, 52 for 1Y, 53 for ALL)
  component Heatmap: Item {
    id: heatmap
    property var daily: []
    property int weeks: 5
    readonly property real gap: Style.space(2)
    readonly property int rows: 7
    readonly property real cellSize: Math.max(3, Math.min(16, Math.floor((width - (heatmap.weeks - 1) * gap) / heatmap.weeks)))

    readonly property var tokenMap: {
      var m = {}
      if (daily) {
        for (var i = 0; i < daily.length; i++) m[daily[i].date] = Number(daily[i].tokens || 0)
      }
      return m
    }

    readonly property real maxVal: {
      var mx = 0
      if (daily) {
        for (var i = 0; i < daily.length; i++) mx = Math.max(mx, Number(daily[i].tokens || 0))
      }
      return mx
    }

    // Build cells: weeks as columns (left=oldest), 7 rows = Mon..Sun.
    readonly property var cells: {
      var cells = []
      if (!daily || daily.length === 0) return cells
      var today = new Date()
      var max = heatmap.maxVal || 1
      var todayDow = (today.getDay() + 6) % 7  // Mon=0
      var maxCol = heatmap.weeks - 1
      // Today is at row=todayDow, col=maxCol (last column)
      for (var r = 0; r < 7; r++) {        // rows = Mon..Sun (0..6)
        for (var c = 0; c < heatmap.weeks; c++) {  // cols = weeks (left=oldest)
          var daysBack = (maxCol - c) * 7 + (todayDow - r)
          var future = daysBack < 0
          var t = future ? 0 : (heatmap.tokenMap[formatDateKey(today, -daysBack)] || 0)
          var level = 0
          if (!future && t > 0) {
            var ratio = t / max
            if (ratio >= 0.75) level = 4
            else if (ratio >= 0.5) level = 3
            else if (ratio >= 0.25) level = 2
            else level = 1
          }
          cells.push({ level: level, future: future, row: r, col: c })
        }
      }
      return cells
    }

    function formatDateKey(ref, daysBack) {
      var d = new Date(ref.getTime() + daysBack * 86400000)
      return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0")
    }

    implicitHeight: heatmap.rows * heatmap.cellSize + (heatmap.rows - 1) * gap
    implicitWidth: heatmap.weeks * heatmap.cellSize + (heatmap.weeks - 1) * gap

    // Render grid - row-major order (Grid fills row by row)
    Grid {
      id: heatmapGrid
      columns: heatmap.weeks
      anchors.centerIn: parent
      rowSpacing: heatmap.gap
      columnSpacing: heatmap.gap
      Repeater {
        model: heatmap.cells.length
        delegate: Rectangle {
          required property int index
          width: heatmap.cellSize
          height: heatmap.cellSize
          radius: Style.space(2)
          color: {
            if (heatmap.cells[index].future) return "transparent"
            if (heatmap.cells[index].level === 0) return root.alpha(root.foreground, 0.07)
            if (heatmap.cells[index].level === 1) return root.alpha(root.foreground, 0.30)
            if (heatmap.cells[index].level === 2) return root.alpha(root.foreground, 0.55)
            if (heatmap.cells[index].level === 3) return root.alpha(root.foreground, 0.78)
            return root.foreground
          }
        }
      }
    }

    // Weekday labels (Mon/Wed/Fri on left)
    Column {
      anchors.left: heatmapGrid.left
      anchors.leftMargin: -Style.space(16)
      anchors.top: heatmapGrid.top
      spacing: heatmap.gap
      Repeater {
        model: 7
        Text {
          required property int index
          visible: index === 0 || index === 2 || index === 4
          textFormat: Text.PlainText
          text: index === 0 ? "M" : index === 2 ? "W" : "F"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignRight
          rightPadding: Style.space(4)
          height: heatmap.cellSize
          verticalAlignment: Text.AlignVCenter
        }
      }
    }
  }
}