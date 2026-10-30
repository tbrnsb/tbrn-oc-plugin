import QtQuick
import Quickshell
import Quickshell.Io

// Runs collect.py on a timer and on manual refresh, parses its one JSON
// record, and exposes it as `record`. Deliberately simple: unlike
// omarchy.agents (which discovers many files), this is a single subprocess
// that prints a single JSON object.
Item {
  id: root
  visible: false

  property var settings: ({})

  // The parsed contract record, or null before the first successful run.
  property var record: null
  // Bumped every time `record` is replaced, so views re-evaluate.
  property int revision: 0

  readonly property string scriptPath:
    Qt.resolvedUrl("collect.py").toString().replace("file://", "")

  readonly property int refreshIntervalSec: Math.max(30, Number(settings.refreshIntervalSec ?? 900))

  // Project filter for collector ("all" or project_id)
  property string projectFilter: "all"

  // Coalesce refresh requests: if a run is in flight, remember that another
  // was asked for and do exactly one more when it exits.
  property bool pendingRefresh: false

  Process {
    id: runner
    running: false
    command: ["/usr/bin/python3", root.scriptPath, root.projectFilter]

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyRecord(text)
    }

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("tbrn.oc-plugin", text.trim())
    }

    onExited: {
      if (root.pendingRefresh) {
        root.pendingRefresh = false
        Qt.callLater(root.startRun)
      }
    }
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refreshAll()
  }

  function startRun() {
    if (runner.running) return
    runner.running = true
  }

  // Replace the record only on a successful parse. A failed run leaves the
  // last-good record in place so the widget never blanks out permanently.
  function applyRecord(text) {
    try {
      var parsed = JSON.parse(String(text || ""))
      if (parsed && typeof parsed === "object" && parsed.allTime !== undefined) {
        record = parsed
        revision++
      }
    } catch (e) {
      console.warn("tbrn.oc-plugin", "bad JSON from collector:", e)
    }
  }

  function refreshAll() {
    if (runner.running) {
      pendingRefresh = true
      return
    }
    startRun()
  }
}
