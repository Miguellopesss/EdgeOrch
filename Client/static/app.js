const bootstrap = window.APP_BOOTSTRAP || {};

const state = {
    machines: [],
    selectedVmid: "",
    shellPromptTarget: "",
    refreshTimer: null,
    terminal: null,
    fitAddon: null,
    socket: null,
    connectedVmid: "",
    terminalPlaceholderMode: "no_machine",
    terminalPlaceholderVmid: "",
    postShellRefreshTimers: [],
    actionConfirmResolver: null,
    leaseStatusTimer: null,
    leaseTickTimer: null,
    leasePrompt: null,
    activeLeaseAction: null,
    lastLeaseEventId: "",
    leaseDecisionPending: "",
    rebalanceStatusTimer: null,
    rebalanceCountdownTimer: null,
    rebalanceProposal: null,
    activeMigration: null,
    lastCompletedMigrationId: "",
    rebalanceDecisionPending: "",
    pendingMachineActions: {},
    provisioningOverlayOwner: "",
};

const ui = {
    machineList: document.getElementById("machineList"),
    machineCountBadge: document.getElementById("machineCountBadge"),
    refreshMachinesButton: document.getElementById("refreshMachinesButton"),
    refreshEmptyStateButton: document.getElementById("refreshEmptyStateButton"),
    toastContainer: document.getElementById("toastContainer"),
    emptyStatePanel: document.getElementById("emptyStatePanel"),
    machineWorkspace: document.getElementById("machineWorkspace"),
    machineActionToolbar: document.getElementById("machineActionToolbar"),
    machineLeasePill: document.getElementById("machineLeasePill"),
    machineLeaseCountdown: document.getElementById("machineLeaseCountdown"),
    primaryMachineActionButton: document.getElementById("primaryMachineActionButton"),
    createMachineModal: document.getElementById("createMachineModal"),
    createMachineForm: document.getElementById("createMachineForm"),
    createMachineButton: document.getElementById("createMachineButton"),
    openCreateMachineButton: document.getElementById("openCreateMachineButton"),
    openCreateMachineEmptyButton: document.getElementById("openCreateMachineEmptyButton"),
    closeCreateMachineButton: document.getElementById("closeCreateMachineButton"),
    cancelCreateMachineButton: document.getElementById("cancelCreateMachineButton"),
    machineTitle: document.getElementById("machineTitle"),
    machineSubtitle: document.getElementById("machineSubtitle"),
    detailVmid: document.getElementById("detailVmid"),
    detailIp: document.getElementById("detailIp"),
    detailState: document.getElementById("detailState"),
    detailNode: document.getElementById("detailNode"),
    detailCpu: document.getElementById("detailCpu"),
    detailMemory: document.getElementById("detailMemory"),
    detailDisk: document.getElementById("detailDisk"),
    detailNetwork: document.getElementById("detailNetwork"),
    detailMessage: document.getElementById("detailMessage"),
    connectShellButton: document.getElementById("connectShellButton"),
    disconnectShellButton: document.getElementById("disconnectShellButton"),
    terminalLabel: document.getElementById("terminalLabel"),
    terminalStateBadge: document.getElementById("terminalStateBadge"),
    terminalPlaceholder: document.getElementById("terminalPlaceholder"),
    terminalPlaceholderBadge: document.getElementById("terminalPlaceholderBadge"),
    terminalPlaceholderTitle: document.getElementById("terminalPlaceholderTitle"),
    terminalPlaceholderMessage: document.getElementById("terminalPlaceholderMessage"),
    terminalContainer: document.getElementById("terminalContainer"),
    provisioningOverlay: document.getElementById("provisioningOverlay"),
    provisioningOverlayTitle: document.getElementById("provisioningOverlayTitle"),
    provisioningOverlayMessage: document.getElementById("provisioningOverlayMessage"),
    shellPasswordModal: document.getElementById("shellPasswordModal"),
    shellPasswordForm: document.getElementById("shellPasswordForm"),
    shellPasswordInput: document.getElementById("shellPasswordInput"),
    shellPasswordHint: document.getElementById("shellPasswordHint"),
    cancelShellPasswordButton: document.getElementById("cancelShellPasswordButton"),
    actionConfirmModal: document.getElementById("actionConfirmModal"),
    actionConfirmEyebrow: document.getElementById("actionConfirmEyebrow"),
    actionConfirmTitle: document.getElementById("actionConfirmTitle"),
    actionConfirmMessage: document.getElementById("actionConfirmMessage"),
    actionConfirmNote: document.getElementById("actionConfirmNote"),
    confirmActionButton: document.getElementById("confirmActionButton"),
    cancelActionConfirmButton: document.getElementById("cancelActionConfirmButton"),
    closeActionConfirmButton: document.getElementById("closeActionConfirmButton"),
    actionButtons: Array.from(document.querySelectorAll(".machine-action-button")),
    leaseRenewalCard: document.getElementById("leaseRenewalCard"),
    leaseRenewalTitle: document.getElementById("leaseRenewalTitle"),
    leaseRenewalMessage: document.getElementById("leaseRenewalMessage"),
    leaseRenewalCountdown: document.getElementById("leaseRenewalCountdown"),
    leaseRenewalMachine: document.getElementById("leaseRenewalMachine"),
    leaseRenewalExtension: document.getElementById("leaseRenewalExtension"),
    leaseRenewalNote: document.getElementById("leaseRenewalNote"),
    leaseRenewButton: document.getElementById("leaseRenewButton"),
    leaseDeclineButton: document.getElementById("leaseDeclineButton"),
    rebalanceConsentCard: document.getElementById("rebalanceConsentCard"),
    rebalanceConsentTitle: document.getElementById("rebalanceConsentTitle"),
    rebalanceConsentMessage: document.getElementById("rebalanceConsentMessage"),
    rebalanceConsentCountdown: document.getElementById("rebalanceConsentCountdown"),
    rebalanceConsentSource: document.getElementById("rebalanceConsentSource"),
    rebalanceConsentTarget: document.getElementById("rebalanceConsentTarget"),
    rebalanceConsentNote: document.getElementById("rebalanceConsentNote"),
    rebalanceAcceptButton: document.getElementById("rebalanceAcceptButton"),
    rebalanceDeclineButton: document.getElementById("rebalanceDeclineButton"),
};

const STATUS_STYLES = {
    success: "border-emerald-400/40 bg-emerald-400/12 text-emerald-50 shadow-[0_18px_45px_rgba(16,185,129,0.18)]",
    error: "border-rose-400/40 bg-rose-400/12 text-rose-50 shadow-[0_18px_45px_rgba(244,63,94,0.18)]",
    info: "border-frost-300/40 bg-frost-300/12 text-frost-50 shadow-[0_18px_45px_rgba(34,211,238,0.16)]",
};


function resolveTerminalConstructor() {
    if (typeof window.Terminal === "function") {
        return window.Terminal;
    }

    if (window.Terminal && typeof window.Terminal.Terminal === "function") {
        return window.Terminal.Terminal;
    }

    if (window.XTerm && typeof window.XTerm.Terminal === "function") {
        return window.XTerm.Terminal;
    }

    return null;
}


function resolveFitAddonConstructor() {
    if (window.FitAddon && typeof window.FitAddon.FitAddon === "function") {
        return window.FitAddon.FitAddon;
    }

    if (typeof window.FitAddon === "function") {
        return window.FitAddon;
    }

    return null;
}


function ensureTerminal() {
    if (state.terminal) {
        return;
    }

    const TerminalCtor = resolveTerminalConstructor();
    const FitAddonCtor = resolveFitAddonConstructor();

    if (!TerminalCtor || !FitAddonCtor) {
        throw new Error("The web terminal did not load correctly. Refresh the page and try again.");
    }

    state.terminal = new TerminalCtor({
        cursorBlink: true,
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 14,
        theme: {
            background: "#020617",
            foreground: "#d6e7f0",
            cursor: "#f5c975",
            selectionBackground: "rgba(107, 216, 231, 0.28)",
            black: "#0f172a",
            red: "#fb7185",
            green: "#4ade80",
            yellow: "#facc15",
            blue: "#60a5fa",
            magenta: "#f472b6",
            cyan: "#22d3ee",
            white: "#e2e8f0",
            brightBlack: "#475569",
            brightRed: "#fda4af",
            brightGreen: "#86efac",
            brightYellow: "#fde68a",
            brightBlue: "#93c5fd",
            brightMagenta: "#f9a8d4",
            brightCyan: "#67e8f9",
            brightWhite: "#f8fafc",
        },
    });
    state.fitAddon = new FitAddonCtor();
    state.terminal.loadAddon(state.fitAddon);
    state.terminal.open(ui.terminalContainer);
    state.terminal.options.disableStdin = true;
    state.fitAddon.fit();

    state.terminal.onData((data) => {
        if (state.socket && state.socket.readyState === WebSocket.OPEN) {
            state.socket.send(JSON.stringify({ type: "input", data }));
        }
    });

    window.addEventListener("resize", fitTerminal);
}


function setTerminalInteractive(isInteractive) {
    if (state.terminal) {
        state.terminal.options.disableStdin = !isInteractive;
        if (isInteractive) {
            state.terminal.focus();
        } else {
            state.terminal.blur();
        }
    }

    ui.terminalContainer.classList.toggle("opacity-100", isInteractive);
    ui.terminalContainer.classList.toggle("opacity-20", !isInteractive);
}


function showTerminalPlaceholder(mode = "ready", machine = null) {
    const machineName = machine?.hostname || machine?.vmid || "this machine";
    const messages = {
        no_machine: {
            badge: "SSH",
            title: "Select a machine",
            message: "Pick a machine from the left sidebar to activate the in-browser SSH terminal.",
        },
        ready: {
            badge: "SSH",
            title: "Ready to connect to the guest",
            message: "Select a running machine and open the shell. You will only be asked for the root password at that point.",
        },
        closed: {
            badge: "Session closed",
            title: "Shell closed",
            message: `The connection to ${machineName} was closed. Use "Open shell" to start a new session.`,
        },
        ended: {
            badge: "Session ended",
            title: "Connection ended",
            message: `The SSH session for ${machineName} ended. You can open the shell again whenever you want.`,
        },
        stopped: {
            badge: "Machine stopped",
            title: "Guest is powered off",
            message: `${machineName} is currently stopped. Use "Start machine" to boot the CT again.`,
        },
        stopped_from_shell: {
            badge: "Session ended",
            title: "Machine shut down from shell",
            message: `${machineName} was shut down from the shell. Once the panel refreshes, you can start the machine again here.`,
        },
        migrating: {
            badge: "Migration running",
            title: "Machine temporarily locked",
            message: `${machineName} is being migrated to another node. When the operation finishes, the panel refreshes the data and unlocks the shell again.`,
        },
        lease_expired: {
            badge: "Lease expired",
            title: "Machine being removed",
            message: `${machineName} reached the end of its lease and EdgeOrch is removing it automatically.`,
        },
        error: {
            badge: "Connection failed",
            title: "Could not open the shell",
            message: `The terminal for ${machineName} did not become active. Check the root password and try again.`,
        },
    };
    const content = messages[mode] || messages.ready;

    ui.terminalPlaceholderBadge.textContent = content.badge;
    ui.terminalPlaceholderTitle.textContent = content.title;
    ui.terminalPlaceholderMessage.textContent = content.message;
    state.terminalPlaceholderMode = mode;
    state.terminalPlaceholderVmid = machine?.vmid || "";
    ui.terminalPlaceholder.classList.remove("hidden");
    setTerminalInteractive(false);
}


function hideTerminalPlaceholder() {
    state.terminalPlaceholderMode = "hidden";
    state.terminalPlaceholderVmid = "";
    ui.terminalPlaceholder.classList.add("hidden");
}


function schedulePostShellRefresh() {
    state.postShellRefreshTimers.forEach((timerId) => window.clearTimeout(timerId));
    state.postShellRefreshTimers = [
        window.setTimeout(() => refreshMachines(false), 2000),
        window.setTimeout(() => refreshMachines(false), 7000),
    ];
}


function updatePrimaryActionButton(machine) {
    if (!ui.primaryMachineActionButton) {
        return;
    }

    const status = String(machine?.machine_status || "").toLowerCase();
    const isRunning = status === "running";
    const isStopped = status === "stopped";
    const pendingAction = getPendingMachineAction(machine?.vmid);
    const pendingActionName = String(pendingAction?.action || "");

    ui.primaryMachineActionButton.dataset.actionButton = isStopped ? "start" : "reboot";
    ui.primaryMachineActionButton.textContent = pendingAction && ["start", "reboot"].includes(pendingActionName)
        ? getPendingActionLabel(pendingAction.action)
        : isStopped ? "Start machine" : "Reboot";
    ui.primaryMachineActionButton.className = `machine-action-button rounded-2xl border px-4 py-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${
        isStopped
            ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100 hover:border-emerald-300/55 hover:bg-emerald-300/20"
            : "border-frost-300/25 bg-frost-300/10 text-frost-100 hover:border-frost-300/55 hover:bg-frost-300/20"
    }`;
    ui.primaryMachineActionButton.disabled = Boolean(pendingAction) || !machine || (!isRunning && !isStopped);
}


function fitTerminal(notifyServer = true) {
    if (!state.terminal || !state.fitAddon) {
        return;
    }

    state.fitAddon.fit();
    if (notifyServer && state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(
            JSON.stringify({
                type: "resize",
                cols: state.terminal.cols,
                rows: state.terminal.rows,
            })
        );
    }
}


function showBanner(message, tone = "info") {
    const translatedMessage = translateInterfaceMessage(message);
    if (!ui.toastContainer || !translatedMessage) {
        return;
    }

    const toast = document.createElement("div");
    toast.className = `pointer-events-auto overflow-hidden rounded-[1.6rem] border backdrop-blur-xl transition duration-300 ${STATUS_STYLES[tone] || STATUS_STYLES.info}`;

    const heading = tone === "error" ? "Error" : tone === "success" ? "Success" : "Update";
    toast.innerHTML = `
        <div class="flex items-start gap-3 px-4 py-4">
            <div class="mt-0.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[10px] uppercase tracking-[0.28em] text-white/80">
                ${escapeHtml(heading)}
            </div>
            <p class="flex-1 text-sm leading-7 text-white/95">${escapeHtml(translatedMessage)}</p>
        </div>
    `;

    ui.toastContainer.appendChild(toast);

    const duration = tone === "error" ? 6500 : 4200;
    window.setTimeout(() => {
        toast.classList.add("translate-y-[-10px]", "opacity-0");
        window.setTimeout(() => toast.remove(), 250);
    }, duration);
}


function syncBodyLock() {
    const shouldLock = !ui.createMachineModal.classList.contains("hidden")
        || !ui.shellPasswordModal.classList.contains("hidden")
        || !ui.provisioningOverlay.classList.contains("hidden");
    document.body.classList.toggle("overflow-hidden", shouldLock);
}


function clearBanner() {
    if (!ui.toastContainer) {
        return;
    }
    ui.toastContainer.innerHTML = "";
}


function translateInterfaceMessage(message) {
    const text = String(message || "").trim();
    if (!text) {
        return "";
    }

    const exact = {
        "CT criado com sucesso.": "CT created successfully.",
        "CT apagado com sucesso.": "CT deleted successfully.",
        "CT desligado com sucesso.": "CT shut down successfully.",
        "CT reiniciado com sucesso.": "CT rebooted successfully.",
        "CT migrado com sucesso.": "CT migrated successfully.",
        "CT estava parado e foi iniciado com sucesso.": "CT was stopped and started successfully.",
        "Evento de validade concluido.": "Lease event completed.",
        "Pedido publicado com sucesso.": "Request published successfully.",
        "Resultados privados obtidos com sucesso.": "Private results loaded successfully.",
        "Machine inventory refreshed successfully.": "Machine inventory refreshed successfully.",
    };
    if (exact[text]) {
        return exact[text];
    }

    return text
        .replaceAll("Erro de ligacao", "Connection error")
        .replaceAll("Nao foi possivel", "Could not")
        .replaceAll("não foi possível", "could not")
        .replaceAll("nao foi possivel", "could not")
        .replaceAll("com sucesso", "successfully")
        .replaceAll("criado", "created")
        .replaceAll("apagado", "deleted")
        .replaceAll("desligado", "shut down")
        .replaceAll("reiniciado", "rebooted")
        .replaceAll("migrado", "migrated")
        .replaceAll("pedido", "request")
        .replaceAll("resultado", "result")
        .replaceAll("recurso", "resource");
}


function getSelectedMachine() {
    return state.machines.find((machine) => machine.vmid === state.selectedVmid) || null;
}


function getActiveMigrationForMachine(vmid) {
    if (!state.activeMigration) {
        return null;
    }

    return String(state.activeMigration.vmid || "") === String(vmid || "") ? state.activeMigration : null;
}


function isMachineLockedForMigration(vmid) {
    const activeMigration = getActiveMigrationForMachine(vmid);
    return Boolean(activeMigration && String(activeMigration.status || "") === "running");
}


function getPendingMachineAction(vmid) {
    const normalizedVmid = String(vmid || "");
    return normalizedVmid ? state.pendingMachineActions[normalizedVmid] || null : null;
}


function getPendingActionLabel(actionName) {
    if (actionName === "start") return "Starting...";
    if (actionName === "reboot") return "Rebooting...";
    if (actionName === "shutdown") return "Shutting down...";
    if (actionName === "delete") return "Deleting...";
    return "Working...";
}


function getActionButtonDefaultLabel(actionName) {
    if (actionName === "shutdown") return "Shutdown";
    if (actionName === "delete") return "Delete machine";
    return "";
}


function getMachineActionStamp(machine) {
    if (!machine) {
        return "";
    }
    return [
        String(machine.last_action || ""),
        String(machine.last_updated || ""),
        String(machine.request_id || ""),
        String(machine.last_result_status || ""),
    ].join("|");
}


function getExpectedStatusForAction(actionName) {
    if (actionName === "start") return "running";
    if (actionName === "shutdown") return "stopped";
    if (actionName === "delete") return "deleted";
    return "";
}


function reconcilePendingMachineActions() {
    const liveVmids = new Set(state.machines.map((machine) => String(machine.vmid || "")));
    Object.keys(state.pendingMachineActions).forEach((vmid) => {
        if (!liveVmids.has(vmid)) {
            delete state.pendingMachineActions[vmid];
        }
    });

    state.machines.forEach((machine) => {
        const vmid = String(machine.vmid || "");
        const pendingAction = getPendingMachineAction(vmid);
        if (!pendingAction) {
            return;
        }

        const currentStatus = String(machine.machine_status || "").toLowerCase();
        const expectedStatus = String(pendingAction.expectedStatus || "").toLowerCase();
        const currentStamp = getMachineActionStamp(machine);
        const actionStampChanged = currentStamp && currentStamp !== String(pendingAction.initialActionStamp || "");
        const actionName = String(pendingAction.action || "");
        if (
            (expectedStatus && currentStatus === expectedStatus)
            || (actionName === "delete" && String(machine.last_action || "") === "delete_lxc")
            || (actionName === "reboot" && actionStampChanged)
            || (currentStatus && currentStatus !== String(pendingAction.initialStatus || "").toLowerCase())
        ) {
            delete state.pendingMachineActions[vmid];
        }
    });
}


function getActiveLeaseActionForMachine(vmid) {
    if (!state.activeLeaseAction) {
        return null;
    }

    return String(state.activeLeaseAction.vmid || "") === String(vmid || "") ? state.activeLeaseAction : null;
}


function isMachineLockedForLease(vmid) {
    const activeLeaseAction = getActiveLeaseActionForMachine(vmid);
    return Boolean(activeLeaseAction && String(activeLeaseAction.status || "") === "running");
}


function isMachineLocked(vmid) {
    return isMachineLockedForMigration(vmid) || isMachineLockedForLease(vmid);
}


function machineStatusLabel(machine) {
    if (isMachineLockedForLease(machine?.vmid)) return "Expiring";
    if (isMachineLockedForMigration(machine?.vmid)) return "Migrating";
    const status = String(machine.machine_status || "unknown").toLowerCase();
    if (status === "running") return "Running";
    if (status === "stopped") return "Stopped";
    if (status === "deleted") return "Deleted";
    return status || "Unknown";
}


function machineStatusClasses(machine) {
    if (isMachineLockedForLease(machine?.vmid)) return "border-rose-300/35 bg-rose-300/10 text-rose-100";
    if (isMachineLockedForMigration(machine?.vmid)) return "border-frost-300/35 bg-frost-300/10 text-frost-100";
    const status = String(machine.machine_status || "unknown").toLowerCase();
    if (status === "running") return "border-emerald-400/35 bg-emerald-400/10 text-emerald-100";
    if (status === "stopped") return "border-amber-300/35 bg-amber-300/10 text-amber-100";
    return "border-white/10 bg-white/5 text-slate-300";
}


function formatMemoryLabel(memoryMb) {
    const value = Number(memoryMb || 0);
    if (!Number.isFinite(value) || value <= 0) {
        return "-";
    }
    if (value >= 1024) {
        const gb = value / 1024;
        return Number.isInteger(gb) ? `${gb} GB` : `${gb.toFixed(1)} GB`;
    }
    return `${value} MB`;
}


function formatDiskLabel(diskGb) {
    const value = Number(diskGb || 0);
    if (!Number.isFinite(value) || value <= 0) {
        return "-";
    }
    return Number.isInteger(value) ? `${value} GB` : `${value.toFixed(1)} GB`;
}


function formatDurationLabel(totalSeconds) {
    const numericValue = Number(totalSeconds || 0);
    const safeSeconds = Math.max(0, Number.isFinite(numericValue) ? Math.floor(numericValue) : 0);
    const minutes = Math.floor(safeSeconds / 60);
    const seconds = safeSeconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}


function getMachineLease(machine) {
    return machine && typeof machine.lease === "object" ? machine.lease : null;
}


function renderSidebar() {
    ui.machineCountBadge.textContent = String(state.machines.length);

    if (!state.machines.length) {
        ui.machineList.innerHTML = `
            <div class="rounded-[1.75rem] border border-dashed border-white/10 bg-white/[0.03] p-5 text-sm leading-7 text-slate-400">
                <p class="font-medium text-slate-200">You do not have any active machines in this client yet.</p>
                <p class="mt-2">Create the first one to make it appear in this list.</p>
                <button
                    type="button"
                    data-open-create-from-sidebar="true"
                    class="mt-4 w-full rounded-2xl bg-white/5 px-4 py-3 text-sm font-medium text-slate-100 transition hover:bg-white/10"
                >
                    Open form
                </button>
            </div>
        `;

        const sidebarCreateButton = ui.machineList.querySelector("[data-open-create-from-sidebar='true']");
        if (sidebarCreateButton) {
            sidebarCreateButton.addEventListener("click", openCreateMachineModal);
        }
        return;
    }

    ui.machineList.innerHTML = state.machines
        .map((machine) => {
            const selected = machine.vmid === state.selectedVmid;
            return `
                <button
                    type="button"
                    data-machine-vmid="${machine.vmid}"
                    class="machine-list-item w-full rounded-[1.75rem] border p-4 text-left transition ${
                        selected
                            ? "border-frost-300/55 bg-frost-300/12 shadow-halo"
                            : "border-white/10 bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.05]"
                    }"
                >
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <p class="text-lg font-semibold text-white">${escapeHtml(machine.hostname || `ct-${machine.vmid}`)}</p>
                            <p class="mt-1 font-mono text-xs text-slate-400">VMID ${escapeHtml(machine.vmid)}</p>
                        </div>
                        <span class="rounded-full border px-2.5 py-1 text-[10px] uppercase tracking-[0.25em] ${machineStatusClasses(machine)}">
                            ${escapeHtml(machineStatusLabel(machine))}
                        </span>
                    </div>
                    <div class="mt-4 flex items-center justify-between gap-3 text-xs text-slate-400">
                        <span>${escapeHtml(machine.ip_address || "No IP")}</span>
                        <span>${escapeHtml(machine.proxmox_node || "-")}</span>
                    </div>
                </button>
            `;
        })
        .join("");

    document.querySelectorAll(".machine-list-item").forEach((button) => {
        button.addEventListener("click", () => {
            const vmid = button.getAttribute("data-machine-vmid") || "";
            selectMachine(vmid, { promptShell: false });
        });
    });
}


function renderMachineLeasePill(machine) {
    const lease = getMachineLease(machine);
    const activeLeaseAction = machine ? getActiveLeaseActionForMachine(machine.vmid) : null;

    if (!machine || !lease) {
        ui.machineLeasePill.classList.add("hidden");
        return;
    }

    ui.machineLeasePill.classList.remove("hidden");

    const remainingLabel = activeLeaseAction
        ? "Removing"
        : formatDurationLabel(lease.remaining_seconds);

    ui.machineLeaseCountdown.textContent = remainingLabel;
    ui.machineLeasePill.className = `inline-flex items-center justify-between gap-3 rounded-2xl border px-4 py-2.5 text-left sm:min-w-[9rem] ${
        activeLeaseAction
            ? "border-rose-300/25 bg-rose-300/12"
            : lease.prompt_active
                ? "border-amber-300/25 bg-amber-300/12"
                : "border-emerald-300/20 bg-emerald-300/10"
    }`;
}


function renderWorkspaceState() {
    const machine = getSelectedMachine();
    const hasMachines = state.machines.length > 0;

    ui.emptyStatePanel.classList.toggle("hidden", hasMachines);
    ui.machineWorkspace.classList.toggle("hidden", !hasMachines);
    ui.machineActionToolbar.classList.toggle("hidden", !machine);
}


function renderMachineDetails() {
    const machine = getSelectedMachine();
    const activeMigration = machine ? getActiveMigrationForMachine(machine.vmid) : null;
    const activeLeaseAction = machine ? getActiveLeaseActionForMachine(machine.vmid) : null;
    const lease = getMachineLease(machine);
    const migrationLocked = Boolean(activeMigration && String(activeMigration.status || "") === "running");
    const leaseLocked = Boolean(activeLeaseAction && String(activeLeaseAction.status || "") === "running");
    const pendingAction = getPendingMachineAction(machine?.vmid);
    const actionLocked = Boolean(pendingAction);
    const machineLocked = migrationLocked || leaseLocked;

    if (!machine) {
        if (!state.machines.length) {
            ui.machineTitle.textContent = "No machines yet";
            ui.machineSubtitle.textContent = "Create your first machine to unlock the detail panel and the terminal.";
        } else {
            ui.machineTitle.textContent = "Select a machine";
            ui.machineSubtitle.textContent = "Choose a machine from the left sidebar to view details, actions, and shell access.";
        }

        ui.detailVmid.textContent = "-";
        ui.detailIp.textContent = "-";
        ui.detailState.textContent = "-";
        ui.detailNode.textContent = "-";
        ui.detailCpu.textContent = "-";
        ui.detailMemory.textContent = "-";
        ui.detailDisk.textContent = "-";
        ui.detailNetwork.textContent = "-";
        ui.detailMessage.textContent = "No machine is currently selected.";
        renderMachineLeasePill(null);
        ui.connectShellButton.disabled = true;
        ui.disconnectShellButton.disabled = !state.connectedVmid;
        updatePrimaryActionButton(null);
        ui.actionButtons
            .filter((button) => button !== ui.primaryMachineActionButton)
            .forEach((button) => {
                button.disabled = true;
            });
        ui.terminalLabel.textContent = "No machine selected";
        showTerminalPlaceholder("no_machine");
        setTerminalState("offline", "offline");
        renderWorkspaceState();
        return;
    }

    renderMachineLeasePill(machine);
    ui.machineTitle.textContent = machine.hostname || `CT ${machine.vmid}`;
    ui.machineSubtitle.textContent = leaseLocked
        ? `Lease expired. EdgeOrch is removing ${machine.hostname || machine.vmid} automatically.`
        : migrationLocked
        ? `Migration in progress from ${activeMigration.source_node || "-"} to ${activeMigration.target_node || "-"}`
        : lease
            ? `Request ${machine.request_id || "-"} | lease left ${formatDurationLabel(lease.remaining_seconds)}`
            : `Request ${machine.request_id || "-"} | last update ${machine.last_updated || "-"}`;
    ui.detailVmid.textContent = machine.vmid || "-";
    ui.detailIp.textContent = machine.ip_address || "No IP";
    ui.detailState.textContent = machineStatusLabel(machine);
    ui.detailNode.textContent = `${machine.proxmox_node || "-"} / ${machine.node_hostname || "-"}`;
    ui.detailCpu.textContent = machine.cpu ? `${machine.cpu} vCPU` : "-";
    ui.detailMemory.textContent = formatMemoryLabel(machine.memory_mb);
    ui.detailDisk.textContent = formatDiskLabel(machine.disk_gb);
    ui.detailNetwork.textContent = machine.network || "-";
    ui.detailMessage.textContent = translateInterfaceMessage(leaseLocked
        ? (activeLeaseAction.message || "This machine expired and is being removed automatically.")
        : migrationLocked
        ? `This machine is being migrated from ${activeMigration.source_node_label || activeMigration.source_node || "-"} to ${activeMigration.target_node_label || activeMigration.target_node || "-"}.`
        : actionLocked
        ? `${getPendingActionLabel(pendingAction.action)} Waiting for the node inventory to report a state change.`
        : (machine.last_message || "No message available."));
    ui.connectShellButton.disabled = machineLocked || !machine.shell_ready;
    ui.disconnectShellButton.disabled = state.connectedVmid !== machine.vmid;
    updatePrimaryActionButton(machineLocked ? null : machine);
    ui.actionButtons
        .filter((button) => button !== ui.primaryMachineActionButton)
        .forEach((button) => {
            const actionName = button.dataset.actionButton || "";
            if (machineLocked) {
                button.disabled = true;
                return;
            }
            if (actionLocked && ["start", "reboot", "shutdown", "delete"].includes(actionName)) {
                button.textContent = actionName === pendingAction.action
                    ? getPendingActionLabel(pendingAction.action)
                    : getActionButtonDefaultLabel(actionName);
                button.disabled = true;
                return;
            }
            button.textContent = getActionButtonDefaultLabel(actionName);
            if (actionName === "shutdown") {
                button.disabled = String(machine.machine_status || "").toLowerCase() !== "running";
                return;
            }
            button.disabled = false;
        });
    ui.terminalLabel.textContent = machine.shell_ready
        ? `Shell for ${machine.hostname || machine.vmid} (${machine.ip_address})`
        : String(machine.machine_status || "").toLowerCase() === "stopped"
            ? `Shell unavailable while ${machine.hostname || machine.vmid} is stopped`
            : `Shell unavailable for ${machine.hostname || machine.vmid}`;

    if (state.connectedVmid !== machine.vmid) {
        const machineStatus = String(machine.machine_status || "").toLowerCase();
        const preserveMode = !machineLocked
            && machineStatus !== "stopped"
            && state.terminalPlaceholderVmid === machine.vmid
            && ["closed", "ended", "error"].includes(state.terminalPlaceholderMode);
        showTerminalPlaceholder(
            leaseLocked
                ? "lease_expired"
                : migrationLocked
                ? "migrating"
                : preserveMode
                ? state.terminalPlaceholderMode
                : machineStatus === "stopped"
                    ? "stopped"
                    : (machine.shell_ready ? "ready" : "error"),
            machine
        );
        setTerminalState("offline", "offline");
    }

    renderWorkspaceState();
}


function selectMachine(vmid, options = {}) {
    const machine = state.machines.find((item) => item.vmid === vmid) || null;
    state.selectedVmid = machine ? machine.vmid : "";
    renderSidebar();
    renderMachineDetails();

    if (!machine) {
        if (state.connectedVmid) {
            disconnectShell(false);
        }
        return;
    }

    if (state.connectedVmid && state.connectedVmid !== machine.vmid) {
        disconnectShell(false);
    }

    if (options.promptShell && machine.shell_ready) {
        openShellPasswordModal(machine.vmid);
    }
}


function hideLeaseRenewalCard() {
    state.leasePrompt = null;
    ui.leaseRenewalCard.classList.add("hidden");
}


function renderLeaseRenewalCard() {
    const prompt = state.leasePrompt;
    if (!prompt || state.activeLeaseAction) {
        hideLeaseRenewalCard();
        return;
    }

    ui.leaseRenewalTitle.textContent = `Renew ${prompt.hostname || prompt.vmid}?`;
    ui.leaseRenewalMessage.textContent = translateInterfaceMessage(
        prompt.message || `The lease for ${prompt.hostname || prompt.vmid} is about to expire.`
    );
    ui.leaseRenewalMachine.textContent = `${prompt.hostname || prompt.vmid} (${prompt.vmid || "-"})`;
    ui.leaseRenewalExtension.textContent = `+${Math.max(1, Math.round(Number(prompt.renew_seconds || bootstrap.machineLeaseSeconds || 300) / 60))} min`;
    ui.leaseRenewalCountdown.textContent = `${Math.max(0, Number(prompt.remaining_seconds || 0))}s`;
    ui.leaseRenewalNote.textContent = "If you decline or do not answer, the machine is removed automatically to free up resources.";
    const isPending = Boolean(state.leaseDecisionPending);
    ui.leaseRenewButton.textContent = state.leaseDecisionPending === "renew" ? "Renewing..." : "Renew now";
    ui.leaseDeclineButton.textContent = state.leaseDecisionPending === "decline" ? "Ending..." : "Do not renew";
    ui.leaseRenewButton.disabled = isPending || Number(prompt.remaining_seconds || 0) <= 0;
    ui.leaseDeclineButton.disabled = isPending || Number(prompt.remaining_seconds || 0) <= 0;
    ui.leaseRenewalCard.classList.remove("hidden");
}


async function refreshLeaseStatus() {
    try {
        const response = await fetch("/api/leases/status", { headers: { Accept: "application/json" } });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Failed to fetch machine lease status.");
        }

        const previousActiveLeaseId = state.activeLeaseAction?.id || "";
        const previousLastEventId = state.lastLeaseEventId;

        state.leasePrompt = payload.prompt || null;
        state.activeLeaseAction = payload.active_enforcement || null;

        if (state.activeLeaseAction && String(state.activeLeaseAction.status || "") === "running") {
            hideLeaseRenewalCard();
            if (state.connectedVmid && String(state.connectedVmid) === String(state.activeLeaseAction.vmid || "")) {
                disconnectShell(false);
            }
        } else {
            renderLeaseRenewalCard();
        }

        if ((state.activeLeaseAction?.id || "") !== previousActiveLeaseId) {
            renderSidebar();
            renderMachineDetails();
        }

        const lastEvent = payload.last_event || null;
        if (lastEvent && lastEvent.id && lastEvent.id !== previousLastEventId) {
            state.lastLeaseEventId = lastEvent.id;
            await refreshMachines(false);
            showBanner(
                lastEvent.message || "Evento de validade concluido.",
                
                String(lastEvent.status || "") === "failed" ? "error" : "success"
            );
        }
    } catch (error) {
        console.error(error);
    }
}


async function renewMachineLease() {
    const prompt = state.leasePrompt;
    if (!prompt) {
        return;
    }

    ui.leaseRenewButton.disabled = true;
    ui.leaseDeclineButton.disabled = true;
    state.leaseDecisionPending = "renew";
    renderLeaseRenewalCard();

    try {
        const response = await fetch(`/api/leases/prompts/${encodeURIComponent(prompt.id)}/renew`, {
            method: "POST",
            headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Failed to renew the machine lease.");
        }

        const inventory = payload.inventory || { machines: [] };
        state.machines = Array.isArray(inventory.machines) ? inventory.machines : state.machines;
        state.leasePrompt = payload.prompt || null;
        state.activeLeaseAction = payload.active_enforcement || null;
        state.leaseDecisionPending = "";
        if (payload.last_event?.id) {
            state.lastLeaseEventId = payload.last_event.id;
        }
        renderSidebar();
        renderMachineDetails();
        showBanner(payload.message || "Machine lease renewed successfully.", "success");
        await refreshLeaseStatus();
    } catch (error) {
        state.leaseDecisionPending = "";
        ui.leaseRenewButton.disabled = false;
        ui.leaseDeclineButton.disabled = false;
        renderLeaseRenewalCard();
        showBanner(String(error.message || error), "error");
    }
}


async function declineMachineLease() {
    const prompt = state.leasePrompt;
    if (!prompt) {
        return;
    }

    ui.leaseRenewButton.disabled = true;
    ui.leaseDeclineButton.disabled = true;
    state.leaseDecisionPending = "decline";
    renderLeaseRenewalCard();

    try {
        const response = await fetch(`/api/leases/prompts/${encodeURIComponent(prompt.id)}/decline`, {
            method: "POST",
            headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Failed to end the machine lease.");
        }

        const inventory = payload.inventory || { machines: [] };
        state.machines = Array.isArray(inventory.machines) ? inventory.machines : state.machines;
        state.leaseDecisionPending = "";
        renderSidebar();
        renderMachineDetails();
        showBanner(payload.message || "The machine will be removed automatically.", "info");
        await refreshLeaseStatus();
    } catch (error) {
        state.leaseDecisionPending = "";
        ui.leaseRenewButton.disabled = false;
        ui.leaseDeclineButton.disabled = false;
        renderLeaseRenewalCard();
        showBanner(String(error.message || error), "error");
    }
}


function startLeasePolling() {
    if (state.leaseStatusTimer) {
        window.clearInterval(state.leaseStatusTimer);
    }
    state.leaseStatusTimer = window.setInterval(() => refreshLeaseStatus(), 4000);
}


function startLeaseTicking() {
    if (state.leaseTickTimer) {
        window.clearInterval(state.leaseTickTimer);
    }

    state.leaseTickTimer = window.setInterval(() => {
        state.machines.forEach((machine) => {
            const lease = getMachineLease(machine);
            if (lease && Number(lease.remaining_seconds || 0) > 0) {
                lease.remaining_seconds = Math.max(0, Number(lease.remaining_seconds || 0) - 1);
            }
        });

        if (state.leasePrompt && Number(state.leasePrompt.remaining_seconds || 0) > 0) {
            state.leasePrompt.remaining_seconds = Math.max(0, Number(state.leasePrompt.remaining_seconds || 0) - 1);
            renderLeaseRenewalCard();
        }

        const selectedMachine = getSelectedMachine();
        if (selectedMachine && getMachineLease(selectedMachine)) {
            renderMachineDetails();
        }
    }, 1000);
}


function hideRebalanceConsentCard() {
    if (state.rebalanceCountdownTimer) {
        window.clearInterval(state.rebalanceCountdownTimer);
        state.rebalanceCountdownTimer = null;
    }
    state.rebalanceProposal = null;
    ui.rebalanceConsentCard.classList.add("hidden");
}


function renderRebalanceConsentCard() {
    const proposal = state.rebalanceProposal;
    if (!proposal || state.activeMigration) {
        hideRebalanceConsentCard();
        return;
    }

    ui.rebalanceConsentTitle.textContent = `Migrate ${proposal.machine.hostname || proposal.machine.vmid}?`;
    ui.rebalanceConsentMessage.textContent = translateInterfaceMessage(
        proposal.message || `EdgeOrch found a possible migration from ${proposal.source_node} to ${proposal.target_node}.`
    );
    ui.rebalanceConsentSource.textContent = `${proposal.source_node} / ${proposal.source_node_label || "-"}`;
    ui.rebalanceConsentTarget.textContent = `${proposal.target_node} / ${proposal.target_node_label || "-"}`;
    ui.rebalanceConsentNote.textContent = "You have 10 seconds to accept or decline. If you do not answer, EdgeOrch accepts this migration automatically.";
    ui.rebalanceConsentCard.classList.remove("hidden");

    if (state.rebalanceCountdownTimer) {
        window.clearInterval(state.rebalanceCountdownTimer);
        state.rebalanceCountdownTimer = null;
    }

    const updateCountdown = () => {
        const remainingSeconds = Math.max(0, Number(proposal.remaining_seconds || 0));
        const isPending = Boolean(state.rebalanceDecisionPending);
        ui.rebalanceConsentCountdown.textContent = `${remainingSeconds}s`;
        ui.rebalanceAcceptButton.textContent =
            state.rebalanceDecisionPending === "accept" ? "Starting..." : "Migrate now";
        ui.rebalanceDeclineButton.textContent =
            state.rebalanceDecisionPending === "decline" ? "Declining..." : "Not now";
        ui.rebalanceAcceptButton.disabled = isPending || remainingSeconds <= 0;
        ui.rebalanceDeclineButton.disabled = isPending || remainingSeconds <= 0;
        if (remainingSeconds <= 0 && !isPending) {
            ui.rebalanceConsentNote.textContent = "No answer was sent. EdgeOrch is accepting this migration.";
            acceptRebalanceProposal();
        }
    };

    updateCountdown();
    state.rebalanceCountdownTimer = window.setInterval(() => {
        if (!state.rebalanceProposal || state.rebalanceProposal.id !== proposal.id) {
            if (state.rebalanceCountdownTimer) {
                window.clearInterval(state.rebalanceCountdownTimer);
                state.rebalanceCountdownTimer = null;
            }
            return;
        }

        state.rebalanceProposal.remaining_seconds = Math.max(
            0,
            Number(state.rebalanceProposal.remaining_seconds || 0) - 1
        );
        updateCountdown();

        if (Number(state.rebalanceProposal.remaining_seconds || 0) <= 0 && state.rebalanceCountdownTimer) {
            window.clearInterval(state.rebalanceCountdownTimer);
            state.rebalanceCountdownTimer = null;
        }
    }, 1000);
}


async function refreshRebalanceStatus() {
    try {
        const response = await fetch("/api/rebalance/status", { headers: { Accept: "application/json" } });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Failed to fetch rebalance status.");
        }

        const previousActiveMigrationId = state.activeMigration?.id || "";
        const previousCompletedMigrationId = state.lastCompletedMigrationId;

        state.rebalanceProposal = payload.proposal || null;
        state.activeMigration = payload.active_migration || null;

        if (state.activeMigration && String(state.activeMigration.status || "") === "running") {
            hideRebalanceConsentCard();
            if (state.connectedVmid && String(state.connectedVmid) === String(state.activeMigration.vmid || "")) {
                disconnectShell(false);
            }
            openProvisioningOverlay(
                "Migration in progress",
                `Moving ${state.activeMigration.hostname || state.activeMigration.vmid} from ${state.activeMigration.source_node || "-"} to ${state.activeMigration.target_node || "-"}. The machine stays locked during this operation.`,
                "migration"
            );
        } else {
            closeProvisioningOverlay("migration");
            renderRebalanceConsentCard();
        }

        if (state.activeMigration?.id !== previousActiveMigrationId) {
            renderSidebar();
            renderMachineDetails();
        }

        const completedMigration = payload.last_completed_migration || null;
        if (completedMigration && completedMigration.id && completedMigration.id !== previousCompletedMigrationId) {
            state.lastCompletedMigrationId = completedMigration.id;
            closeProvisioningOverlay("migration");
            await refreshMachines(false);
            showBanner(
                completedMigration.message || "Migration completed.",
                String(completedMigration.status || "") === "completed" ? "success" : "error"
            );
        }
    } catch (error) {
        console.error(error);
    }
}


async function acceptRebalanceProposal() {
    const proposal = state.rebalanceProposal;
    if (!proposal) {
        return;
    }

    ui.rebalanceAcceptButton.disabled = true;
    ui.rebalanceDeclineButton.disabled = true;
    state.rebalanceDecisionPending = "accept";
    renderRebalanceConsentCard();

    try {
        const response = await fetch(`/api/rebalance/proposals/${encodeURIComponent(proposal.id)}/accept`, {
            method: "POST",
            headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Failed to accept the migration proposal.");
        }

        state.rebalanceDecisionPending = "";
        showBanner(payload.message || "Migration accepted.", "info");
        await refreshRebalanceStatus();
        renderSidebar();
        renderMachineDetails();
    } catch (error) {
        state.rebalanceDecisionPending = "";
        ui.rebalanceAcceptButton.disabled = false;
        ui.rebalanceDeclineButton.disabled = false;
        renderRebalanceConsentCard();
        showBanner(String(error.message || error), "error");
    }
}


async function declineRebalanceProposal() {
    const proposal = state.rebalanceProposal;
    if (!proposal) {
        return;
    }

    ui.rebalanceAcceptButton.disabled = true;
    ui.rebalanceDeclineButton.disabled = true;
    state.rebalanceDecisionPending = "decline";
    renderRebalanceConsentCard();

    try {
        const response = await fetch(`/api/rebalance/proposals/${encodeURIComponent(proposal.id)}/decline`, {
            method: "POST",
            headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.message || "Failed to decline the migration proposal.");
        }

        state.rebalanceDecisionPending = "";
        state.rebalanceProposal = payload.proposal || null;
        state.activeMigration = payload.active_migration || null;
        renderRebalanceConsentCard();
        if (!state.rebalanceProposal) {
            showBanner(payload.message || "Proposal declined.", "info");
        }
    } catch (error) {
        state.rebalanceDecisionPending = "";
        ui.rebalanceAcceptButton.disabled = false;
        ui.rebalanceDeclineButton.disabled = false;
        renderRebalanceConsentCard();
        showBanner(String(error.message || error), "error");
    }
}


function startRebalancePolling() {
    if (state.rebalanceStatusTimer) {
        window.clearInterval(state.rebalanceStatusTimer);
    }
    state.rebalanceStatusTimer = window.setInterval(() => refreshRebalanceStatus(), 4000);
}


function setLoading(button, loading, originalText) {
    if (!button) return;

    if (loading) {
        button.dataset.originalText = originalText || button.textContent;
        button.disabled = true;
        button.textContent = "Processing...";
        return;
    }

    button.disabled = false;
    button.textContent = button.dataset.originalText || originalText || button.textContent;
}


async function refreshMachines(showInfo = false) {
    try {
        const response = await fetch("/api/machines");
        const payload = await response.json();
        if (!payload.success) {
            throw new Error(payload.message || "Could not load machines.");
        }

        state.machines = Array.isArray(payload.machines) ? payload.machines : [];
        reconcilePendingMachineActions();
        if (state.selectedVmid && !state.machines.some((machine) => machine.vmid === state.selectedVmid)) {
            state.selectedVmid = "";
            disconnectShell(false);
        }

        if (!state.selectedVmid && state.machines.length) {
            state.selectedVmid = state.machines[0].vmid;
        }

        renderSidebar();
        renderMachineDetails();

        if (showInfo) {
            showBanner(payload.message || "Machine list refreshed.", "info");
        }
    } catch (error) {
        showBanner(String(error.message || error), "error");
    }
}


function openCreateMachineModal() {
    applyDefaultFormValues();
    ui.createMachineModal.style.display = "";
    ui.createMachineModal.classList.remove("hidden");
    ui.createMachineModal.classList.add("flex");
    syncBodyLock();
    window.setTimeout(() => {
        const hostnameInput = ui.createMachineForm.querySelector("#hostname");
        if (hostnameInput) {
            hostnameInput.focus();
            hostnameInput.select();
        }
    }, 50);
}


function closeCreateMachineModal() {
    ui.createMachineModal.style.display = "none";
    ui.createMachineModal.classList.add("hidden");
    ui.createMachineModal.classList.remove("flex");
    syncBodyLock();
}


function openProvisioningOverlay(title, message, owner = "general") {
    state.provisioningOverlayOwner = owner;
    ui.provisioningOverlayTitle.textContent = title;
    ui.provisioningOverlayMessage.textContent = message;
    ui.provisioningOverlay.style.display = "flex";
    ui.provisioningOverlay.classList.remove("hidden");
    ui.provisioningOverlay.classList.add("flex");
    syncBodyLock();
}


function closeProvisioningOverlay(owner = "") {
    if (owner && state.provisioningOverlayOwner && state.provisioningOverlayOwner !== owner) {
        return;
    }
    state.provisioningOverlayOwner = "";
    ui.provisioningOverlay.style.display = "none";
    ui.provisioningOverlay.classList.add("hidden");
    ui.provisioningOverlay.classList.remove("flex");
    syncBodyLock();
}


function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}


function nextPaint() {
    return new Promise((resolve) => {
        window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
    });
}


async function waitWithLeasePolling(ms) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
        await refreshLeaseStatus();
        await wait(Math.min(1000, Math.max(0, deadline - Date.now())));
    }
    await refreshLeaseStatus();
}


async function submitCreateMachine(event) {
    event.preventDefault();
    clearBanner();

    const formData = new FormData(ui.createMachineForm);
    const payload = {
        hostname: String(formData.get("hostname") || "").trim(),
        template: String(formData.get("template") || "").trim(),
        cpu: Number(formData.get("cpu") || 0),
        memory_mb: Number(formData.get("memory_mb") || 0),
        disk_gb: Number(formData.get("disk_gb") || 0),
        network: String(formData.get("network") || "").trim(),
    };

    const rootPassword = String(formData.get("rootPassword") || "");
    if (rootPassword.trim()) {
        payload.root_password = rootPassword;
    }

    try {
        setLoading(ui.createMachineButton, true, "Create machine");
        closeCreateMachineModal();
        openProvisioningOverlay(
            "Creating your machine",
            `Publishing the request for ${payload.hostname || "the new CT"} and keeping this screen open until the new CT is running in the node inventory.`,
            "create"
        );
        await nextPaint();

        const response = await fetch("/api/machines", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.message || "Unexpected failure while creating the machine.");
        }

        if (result.success) {
            ui.provisioningOverlayMessage.textContent = "Machine created and published in the node inventory.";
        }

        const inventory = result.inventory || { machines: [] };
        state.machines = Array.isArray(inventory.machines) ? inventory.machines : [];
        state.selectedVmid = String(result.selected_vmid || state.selectedVmid || "");

        if (!state.selectedVmid && state.machines.length) {
            state.selectedVmid = state.machines[0].vmid;
        }

        renderSidebar();
        renderMachineDetails();
        closeProvisioningOverlay("create");
        window.setTimeout(() => refreshMachines(false), 7000);
        window.setTimeout(() => refreshMachines(false), 15000);
        const leaseMinutes = Math.max(1, Math.round(Number(bootstrap.machineLeaseSeconds || 300) / 60));
        showBanner(
            result.success
                ? `${result.message || "Request sent."} Initial lease: ${leaseMinutes} min.`
                : (result.message || "Request sent."),
            result.success ? "success" : "error"
        );
        ui.createMachineForm.reset();
        applyDefaultFormValues();
    } catch (error) {
        closeProvisioningOverlay("create");
        closeCreateMachineModal();
        showBanner(String(error.message || error), "error");
    } finally {
        setLoading(ui.createMachineButton, false, "Create machine");
    }
}


async function runMachineAction(actionName, clickedButton = null) {
    const machine = getSelectedMachine();
    if (!machine) {
        showBanner("Select a machine first.", "error");
        return;
    }

    if (isMachineLockedForLease(machine.vmid)) {
        showBanner("This machine lease expired and it is being removed automatically.", "error");
        return;
    }

    if (isMachineLockedForMigration(machine.vmid)) {
        showBanner("This machine is being migrated and is temporarily locked.", "error");
        return;
    }

    if (getPendingMachineAction(machine.vmid)) {
        showBanner("Wait for the machine state to change before running another power action.", "info");
        return;
    }

    const confirmed = await openActionConfirmModal(getActionConfirmConfig(actionName, machine));
    if (!confirmed) {
        return;
    }

    const button = clickedButton;
    const lockUntilStateChange = ["start", "reboot", "shutdown", "delete"].includes(actionName);
    if (lockUntilStateChange) {
        state.pendingMachineActions[String(machine.vmid)] = {
            action: actionName,
            initialStatus: String(machine.machine_status || "").toLowerCase(),
            initialActionStamp: getMachineActionStamp(machine),
            expectedStatus: getExpectedStatusForAction(actionName),
            startedAt: Date.now(),
        };
        renderMachineDetails();
    }
    setLoading(button, true, button ? button.textContent : "");

    try {
        const response = await fetch(`/api/machines/${encodeURIComponent(machine.vmid)}/actions/${encodeURIComponent(actionName)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ hostname: machine.hostname || "" }),
        });
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.message || "Unexpected failure while running the action.");
        }

        const inventory = result.inventory || { machines: [] };
        state.machines = Array.isArray(inventory.machines) ? inventory.machines : [];
        reconcilePendingMachineActions();
        state.selectedVmid = String(result.selected_vmid || "");

        if (actionName === "delete" && result.success) {
            disconnectShell(false);
            delete state.pendingMachineActions[String(machine.vmid)];
        }

        if (actionName === "shutdown" && result.success && state.connectedVmid === machine.vmid) {
            disconnectShell(false);
        }

        if (!state.selectedVmid && state.machines.length) {
            state.selectedVmid = state.machines[0].vmid;
        }

        renderSidebar();
        renderMachineDetails();
        if (lockUntilStateChange && result.success) {
            window.setTimeout(() => refreshMachines(false), 2000);
            window.setTimeout(() => refreshMachines(false), 7000);
            window.setTimeout(() => refreshMachines(false), 15000);
        }
        showBanner(result.message || "Action completed.", result.success ? "success" : "error");
    } catch (error) {
        if (lockUntilStateChange) {
            delete state.pendingMachineActions[String(machine.vmid)];
            renderMachineDetails();
        }
        showBanner(String(error.message || error), "error");
    } finally {
        setLoading(button, false, button ? button.textContent : "");
        renderMachineDetails();
    }
}


function openShellPasswordModal(vmid) {
    const machine = state.machines.find((item) => item.vmid === vmid);
    if (!machine) {
        showBanner("Could not find the selected machine.", "error");
        return;
    }

    if (isMachineLockedForLease(vmid)) {
        showBanner("This machine lease expired and it is being removed automatically.", "error");
        return;
    }

    if (isMachineLockedForMigration(vmid)) {
        showBanner("This machine is being migrated right now.", "error");
        return;
    }

    if (!machine.shell_ready) {
        showBanner("This machine is not ready for SSH yet.", "error");
        return;
    }

    state.shellPromptTarget = vmid;
    ui.shellPasswordHint.textContent = `Enter the root password to open the shell for ${machine.hostname || vmid} (${machine.ip_address || "no IP"}).`;
    ui.shellPasswordInput.value = "";
    ui.shellPasswordModal.classList.remove("hidden");
    ui.shellPasswordModal.classList.add("flex");
    syncBodyLock();
    window.setTimeout(() => ui.shellPasswordInput.focus(), 50);
}


function closeShellPasswordModal() {
    state.shellPromptTarget = "";
    ui.shellPasswordInput.value = "";
    ui.shellPasswordModal.classList.add("hidden");
    ui.shellPasswordModal.classList.remove("flex");
    syncBodyLock();
}


function openActionConfirmModal(config) {
    return new Promise((resolve) => {
        state.actionConfirmResolver = resolve;
        ui.actionConfirmEyebrow.textContent = config.eyebrow || "Confirm";
        ui.actionConfirmTitle.textContent = config.title || "Confirm action";
        ui.actionConfirmMessage.textContent = config.message || "Review this action before continuing.";
        ui.actionConfirmNote.textContent = config.note || "The action will be sent to the provisioning AE and executed in Proxmox.";
        ui.confirmActionButton.textContent = config.confirmLabel || "Confirm";
        ui.confirmActionButton.className = config.confirmClass
            || "rounded-2xl bg-ember-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-ember-300";
        ui.actionConfirmModal.classList.remove("hidden");
        ui.actionConfirmModal.classList.add("flex");
        syncBodyLock();
        window.setTimeout(() => ui.confirmActionButton.focus(), 50);
    });
}


function closeActionConfirmModal(confirmed = false) {
    const resolver = state.actionConfirmResolver;
    state.actionConfirmResolver = null;
    ui.actionConfirmModal.classList.add("hidden");
    ui.actionConfirmModal.classList.remove("flex");
    syncBodyLock();
    if (resolver) {
        resolver(confirmed);
    }
}


function getActionConfirmConfig(actionName, machine) {
    const machineName = machine?.hostname || machine?.vmid || "this machine";
    const base = {
        note: "The action will be sent to the provisioning AE and executed in Proxmox.",
        confirmClass: "rounded-2xl bg-ember-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-ember-300",
    };

    if (actionName === "start") {
        return {
            ...base,
            eyebrow: "Start machine",
            title: `Start ${machineName}`,
            message: `Do you want to start ${machineName} again?`,
            note: "EdgeOrch will boot the stopped CT in Proxmox and wait for it to become running again.",
            confirmLabel: "Start machine",
            confirmClass: "rounded-2xl bg-emerald-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-emerald-300",
        };
    }

    if (actionName === "shutdown") {
        return {
            ...base,
            eyebrow: "Shutdown",
            title: `Shut down ${machineName}`,
            message: `Do you want to shut down ${machineName}?`,
            note: "The CT will receive a clean shutdown. After that, shell access stays unavailable until you start the machine again.",
            confirmLabel: "Shutdown",
            confirmClass: "rounded-2xl bg-amber-300 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-200",
        };
    }

    if (actionName === "delete") {
        return {
            ...base,
            eyebrow: "Delete machine",
            title: `Delete ${machineName}`,
            message: `Do you really want to delete ${machineName}?`,
            note: "This action removes the CT in Proxmox, purges related configuration, and destroys unreferenced guest disks as well.",
            confirmLabel: "Delete machine",
            confirmClass: "rounded-2xl bg-rose-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-rose-300",
        };
    }

    return {
        ...base,
        eyebrow: "Reboot",
        title: `Reboot ${machineName}`,
        message: `Do you want to reboot ${machineName}?`,
        note: "The CT will reboot in Proxmox. If it is stopped, EdgeOrch uses this action to start it again.",
        confirmLabel: "Reboot",
        confirmClass: "rounded-2xl bg-frost-500 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-frost-300",
    };
}


function setTerminalState(label, tone) {
    const toneMap = {
        connected: "bg-emerald-400/10 text-emerald-100",
        connecting: "bg-frost-300/10 text-frost-100",
        offline: "bg-white/5 text-slate-400",
        error: "bg-rose-400/10 text-rose-100",
    };
    ui.terminalStateBadge.className = `rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.25em] ${toneMap[tone] || toneMap.offline}`;
    ui.terminalStateBadge.textContent = label;
}


async function connectShellWithPassword(password) {
    const machine = getSelectedMachine();
    if (!machine) {
        showBanner("Select a machine before opening the shell.", "error");
        return;
    }

    try {
        ensureTerminal();
    } catch (error) {
        showBanner(String(error.message || error), "error");
        return;
    }

    disconnectShell(false);
    hideTerminalPlaceholder();
    setTerminalInteractive(false);
    state.terminal.clear();
    state.terminal.writeln(`Opening shell for ${machine.hostname || machine.vmid}...`);
    setTerminalState("connecting", "connecting");
    fitTerminal(false);

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}${bootstrap.websocketPath}`);
    state.socket = socket;
    state.connectedVmid = machine.vmid;
    renderMachineDetails();

    socket.addEventListener("open", () => {
        socket.send(
            JSON.stringify({
                type: "connect",
                vmid: machine.vmid,
                password,
                cols: state.terminal.cols,
                rows: state.terminal.rows,
            })
        );
    });

    socket.addEventListener("message", (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type === "output") {
            state.terminal.write(payload.data || "");
            return;
        }

        if (payload.type === "status") {
            if (payload.status === "connected") {
                setTerminalState("connected", "connected");
                ui.terminalLabel.textContent = `Shell for ${machine.hostname || machine.vmid} (${machine.ip_address || "no IP"})`;
                ui.disconnectShellButton.disabled = false;
                hideTerminalPlaceholder();
                setTerminalInteractive(true);
                fitTerminal();
                showBanner(payload.message || "SSH shell connected.", "success");
                return;
            }

            if (payload.status === "disconnected") {
                setTerminalState("offline", "offline");
                state.terminal.clear();
                showTerminalPlaceholder("stopped_from_shell", machine);
                schedulePostShellRefresh();
                return;
            }

            if (payload.status === "error") {
                setTerminalState("error", "error");
                state.terminal.clear();
                showTerminalPlaceholder("error", machine);
                showBanner(payload.message || "SSH connection failed.", "error");
            }
        }
    });

    socket.addEventListener("close", () => {
        if (state.socket === socket) {
            state.socket = null;
        }
        if (state.connectedVmid === machine.vmid) {
            state.connectedVmid = "";
        }
        renderMachineDetails();
        if (!(state.terminalPlaceholderVmid === machine.vmid && ["ended", "stopped_from_shell", "error", "closed"].includes(state.terminalPlaceholderMode))) {
            showTerminalPlaceholder("closed", machine);
        }
        schedulePostShellRefresh();
    });

    socket.addEventListener("error", () => {
        showBanner("The terminal WebSocket connection failed.", "error");
        setTerminalState("error", "error");
    });
}


function disconnectShell(showMessage = true) {
    const machine = getSelectedMachine();

    if (state.socket) {
        try {
            if (state.socket.readyState === WebSocket.OPEN) {
                state.socket.send(JSON.stringify({ type: "close" }));
            }
            state.socket.close();
        } catch (error) {
            console.error(error);
        }
        state.socket = null;
    }

    state.connectedVmid = "";
    setTerminalState("offline", "offline");

    if (state.terminal) {
        state.terminal.clear();
    }

    showTerminalPlaceholder(machine ? "closed" : "no_machine", machine || null);

    renderMachineDetails();

    if (showMessage) {
        showBanner("Shell closed.", "info");
    }
}


function applyDefaultFormValues() {
    const fieldDefaults = {
        hostname: bootstrap.defaults.hostname || "ct-demo-01",
        template: bootstrap.defaults.template || "ubuntu-24.04-ssh-enabled",
        cpu: bootstrap.defaults.cpu || 2,
        memory_mb: bootstrap.defaults.memory_mb || 2048,
        disk_gb: bootstrap.defaults.disk_gb || 20,
        network: bootstrap.defaults.network || "vmbr0",
        rootPassword: "",
    };

    Object.entries(fieldDefaults).forEach(([fieldId, value]) => {
        const field = ui.createMachineForm.querySelector(`#${fieldId}`);
        if (field) {
            field.value = value;
        }
    });
}


function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function startAutoRefresh() {
    if (state.refreshTimer) {
        window.clearInterval(state.refreshTimer);
    }
    state.refreshTimer = window.setInterval(() => refreshMachines(false), 12000);
}


function bindEvents() {
    let createModalBackdropPress = false;

    ui.refreshMachinesButton.addEventListener("click", () => refreshMachines(true));
    ui.refreshEmptyStateButton.addEventListener("click", () => refreshMachines(true));
    ui.openCreateMachineButton.addEventListener("click", openCreateMachineModal);
    ui.openCreateMachineEmptyButton.addEventListener("click", openCreateMachineModal);
    ui.closeCreateMachineButton.addEventListener("click", closeCreateMachineModal);
    ui.cancelCreateMachineButton.addEventListener("click", closeCreateMachineModal);
    ui.createMachineForm.addEventListener("submit", submitCreateMachine);
    ui.connectShellButton.addEventListener("click", () => {
        const machine = getSelectedMachine();
        if (machine) {
            openShellPasswordModal(machine.vmid);
        } else {
            showBanner("Select a machine before opening the shell.", "error");
        }
    });
    ui.disconnectShellButton.addEventListener("click", () => disconnectShell(true));
    ui.cancelShellPasswordButton.addEventListener("click", closeShellPasswordModal);
    ui.shellPasswordForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const password = ui.shellPasswordInput.value;
        closeShellPasswordModal();
        connectShellWithPassword(password);
    });
    ui.actionButtons.forEach((button) => {
        button.addEventListener("click", () => runMachineAction(button.dataset.actionButton || "", button));
    });

    ui.createMachineModal.addEventListener("mousedown", (event) => {
        createModalBackdropPress = event.target === ui.createMachineModal;
    });

    ui.createMachineModal.addEventListener("click", (event) => {
        if (event.target === ui.createMachineModal && createModalBackdropPress) {
            closeCreateMachineModal();
        }
        createModalBackdropPress = false;
    });

    ui.shellPasswordModal.addEventListener("click", (event) => {
        if (event.target === ui.shellPasswordModal) {
            closeShellPasswordModal();
        }
    });

    ui.actionConfirmModal.addEventListener("click", (event) => {
        if (event.target === ui.actionConfirmModal) {
            closeActionConfirmModal(false);
        }
    });

    ui.cancelActionConfirmButton.addEventListener("click", () => closeActionConfirmModal(false));
    ui.closeActionConfirmButton.addEventListener("click", () => closeActionConfirmModal(false));
    ui.confirmActionButton.addEventListener("click", () => closeActionConfirmModal(true));
    ui.leaseRenewButton.addEventListener("click", () => renewMachineLease());
    ui.leaseDeclineButton.addEventListener("click", () => declineMachineLease());
    ui.rebalanceAcceptButton.addEventListener("click", () => acceptRebalanceProposal());
    ui.rebalanceDeclineButton.addEventListener("click", () => declineRebalanceProposal());

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") {
            return;
        }

        if (!ui.actionConfirmModal.classList.contains("hidden")) {
            closeActionConfirmModal(false);
            return;
        }

        if (!ui.shellPasswordModal.classList.contains("hidden")) {
            closeShellPasswordModal();
            return;
        }

        if (!ui.createMachineModal.classList.contains("hidden")) {
            closeCreateMachineModal();
        }
    });
}


async function main() {
    bindEvents();
    applyDefaultFormValues();
    renderSidebar();
    renderMachineDetails();
    syncBodyLock();
    await refreshMachines(false);
    await refreshLeaseStatus();
    await refreshRebalanceStatus();
    startAutoRefresh();
    startLeaseTicking();
    startLeasePolling();
    startRebalancePolling();
}


main().catch((error) => {
    console.error(error);
    showBanner(String(error.message || error), "error");
});

