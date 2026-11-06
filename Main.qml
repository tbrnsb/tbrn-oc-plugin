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

  // True while the collector process runs. The panel shows this as a quiet
  // "refreshing" note so a slow cold scan never looks like a freeze.
  readonly property bool busy: runner.running

  // Empty on success. Set when a run fails (non-zero exit, no output, or
  // unparseable JSON) so the panel can say the data is stale instead of
  // silently pretending everything is fine.
  property string lastError: ""

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
      onStreamFinished: {
        var msg = String(text || "").trim()
        if (msg !== "") console.warn("tbrn.oc-plugin", msg)
      }
    }

    onExited: function(exitCode) {
      if (exitCode !== 0 && root.lastError === "") {
        root.lastError = "collector exited with code " + exitCode
      }
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
    var raw = String(text || "").trim()
    if (raw === "") {
      lastError = "collector returned no output"
      console.warn("tbrn.oc-plugin", "collector returned no output")
      return
    }
    try {
      var parsed = JSON.parse(raw)
      if (parsed && typeof parsed === "object" && parsed.allTime !== undefined) {
        record = parsed
        revision++
        lastError = ""
      } else {
        lastError = "collector JSON missing required fields"
      }
    } catch (e) {
      lastError = "bad JSON from collector"
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
