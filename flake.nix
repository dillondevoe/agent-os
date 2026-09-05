{
  description = "Agent OS — a computer whose shell is an agent, not a desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";  # Dell Latitude = Intel x86_64
      # One module list, two machines: `agentos` ships UNSEALED (provisioning — the model
      # can still be pulled); `agentos-sealed` is the SAME machine with the clean-room
      # egress wall sealed to nixpkgs-only. The on-device seal is therefore a single
      # `nixos-rebuild switch --flake ...#agentos-sealed` AFTER the model pull — no local
      # file editing, no git push.
      baseModules = [
        ./configuration.nix
        ./modules/agent-shell.nix
        ./modules/brain.nix
        ./modules/clean-room.nix
        ./modules/identity.nix   # 324 step 1: first-boot participant minting + self-test (BEFORE audit, which signs as one)
        ./modules/audit.nix
        ./modules/taint.nix
        ./modules/mcp.nix
        ./modules/broker.nix
        ./modules/confirm.nix
        ./modules/seal-check.nix
        ./modules/break-glass.nix   # PR-A: the ONE interactive root door (tty3, password-gated)
        ./modules/mesh-wireguard-sealed.nix  # WP-S1: sealed-lane mesh (options only; enabled per-variant below)
        ./modules/fetch-proxy.nix   # WP-S5: hostname-allowlisted fetch path (options only; INERT unless enabled)
        ./modules/system-set.nix    # PR-A: SCAFFOLD for the root-side system.set executor (impl in PR-J)
        ./modules/boot-branding.nix
      ];
      # Stamp OUR repo revision into every built system.
      #
      # WHY: `nixos-version` reports `<release>.<nixpkgs-date>.<nixpkgs-shortrev>` — the trailing
      # hash is the NIXPKGS pin from flake.lock, never this repo's commit. It therefore reads
      # identically on every build cut from one lockfile and cannot answer "is this box running
      # current config?" even in principle. That cost a full round-trip on the Dell (2026-08-08):
      # `26.11.20260726.624af66` was read as a config rev, but 624af66 is nixpkgs and isn't a
      # valid object in this repo at all, so the intended ancestry check could not be run.
      #
      # Read it back on the box with:  nixos-version --configuration-revision
      # `dirtyShortRev` covers the build-on-the-Dell-live path, where the tree is uncommitted.
      # WP-C3: put that same revision where a human actually LOOKS.
      #
      # `system.configurationRevision` below is only readable via
      # `nixos-version --configuration-revision` — a flag nobody types under pressure. The string
      # on the BOOT MENU and on bare `nixos-version` is `system.nixos.label`, which defaults to
      # `<release>.<nixpkgs-date>.<nixpkgs-shortrev>` — so the hash a human sees is NIXPKGS, and
      # reading it as a config rev is the 2026-08-08 round-trip documented above. The label was
      # not merely unhelpful, it was actively misleading: it LOOKS like an answer to "what config
      # is this box running?" and is an answer to a different question.
      #
      # Adding a TAG rather than setting `label` directly is deliberate. `label` defaults to
      # `<version>` plus the sorted tags, so a tag ADDS the config rev while KEEPING the nixpkgs
      # version that makes a channel bump visible; overriding `label` outright would trade one
      # half-truth for the other. The `cfg-` prefix is the point of the whole change — with two
      # hashes on one line, an unlabelled one is exactly what got misread before.
      #
      # Measured on all four variants, not predicted -- NixOS composes the label as
      # `<sorted-tags>-<version>`, i.e. the tag is PREPENDED. I wrote this comment with it appended
      # and the eval corrected me, which is the only reason the example below is right:
      #
      #   before:  26.11.20260726.624af66
      #   after:   cfg-9540b35-26.11.20260726.624af66
      #
      # On an uncommitted tree `dirtyShortRev` is itself `<rev>-dirty`, so the label reads
      # `cfg-9540b35-dirty-...`. That is wanted, not a defect: a box built from a dirty tree SAYS
      # so on its boot menu, which is exactly the class of question this whole change exists for.
      #
      # Charset: the label lands in the system derivation's store-path NAME, so it is restricted
      # to store-name characters. A git shortRev is hex and `dirty` is alphanumeric, so every value
      # this can produce is safe — and an unsafe one would fail LOUDLY at eval/build, never at boot,
      # so this cannot strand a running box.
      revModule = {
        system.configurationRevision = self.shortRev or self.dirtyShortRev or "dirty";
        system.nixos.tags = [ "cfg-${self.shortRev or self.dirtyShortRev or "dirty"}" ];
      };
      mkSystem = extraModules: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = baseModules ++ [ revModule ] ++ extraModules;
      };
      # OPEN / MESHED variant (`agentos-open`, Dillon msg 8926). A deliberately
      # PERMISSIVE dev box — OpenSSH + Tailscale + full-power user + real bash shell
      # + ollama daemon, NO clean-room egress wall, NO agent-shell REPL, NO auto-pull.
      # Built from a SELF-CONTAINED base (configuration-open.nix) that shares ZERO
      # modules with the sovereign path above, so it can never perturb the sealed
      # surface. Rabbot meshes in over Tailscale/SSH and builds on the Dell live; the
      # box is sealed + repackaged for GitHub at the END, not here.
      openModules = [ ./configuration-open.nix ];
      mkOpenSystem = extraModules: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = openModules ++ [ revModule ] ++ extraModules;
      };
    in {
      # The whole machine. `agent-shell.nix` is the part that makes it Agent OS;
      # `configuration.nix` is boring base plumbing (bootloader, user, network).
      nixosConfigurations.agentos = mkSystem [ ];
      # Same machine, egress wall sealed — the post-model-pull `switch` target.
      nixosConfigurations.agentos-sealed = mkSystem [ {
        agentos.cleanRoom.sealed = true;
        # WP-S1: the sealed box meshes over WireGuard, never Tailscale. Topology (address,
        # peers, key) is injected at deployment time — the public repo ships the mechanism
        # with an empty mesh map (Phase-S "no credentials in the repo" constraint).
        agentos.meshWireguard.enable = true;
      } ];

      # WP-S5 CANDIDATE. `agentos-sealed` above is deliberately UNCHANGED by S5 — the fetch proxy
      # is a separate target until it has been accepted, because switching the deployed sealed
      # variant to it would withdraw root's direct fetch path on a box whose acceptance evidence
      # does not exist yet. Per Geist's 2026-08-14 amendment to spec §WP-S5, S5 may be BUILT while
      # S4 is red; what is gated is what may be DECLARED. So this target exists, evaluates, and is
      # covered by the parse gate and the VM test — and nothing calls it verified, or sealed.
      #
      # The allowlist below is the minimum for a box that can still rebuild itself. It is not a
      # claim about what the Dell needs; that list is a deployment fact and gets settled at the
      # at-the-box acceptance session the spec calls for, not here.
      nixosConfigurations.agentos-sealed-s5 = mkSystem [ {
        agentos.cleanRoom.sealed = true;
        agentos.meshWireguard.enable = true;
        agentos.fetchProxy = {
          enable = true;
          allowedHosts = [ "cache.nixos.org" "channels.nixos.org" ];
        };
      } ];

      # OPEN / MESHED dev variant — intentionally permissive (see `openModules`).
      # Install:  nixos-install --flake github:dillondevoe/agent-os#agentos-open
      # (install.sh VARIANT=agentos-open bakes the mesh authorized_keys + TS_AUTHKEY).
      nixosConfigurations.agentos-open = mkOpenSystem [ ];

      # Prove boot-and-talk in a VM BEFORE it ever touches the Dell:
      #   nix build .#vm && ./result/bin/run-*-vm       (unsealed: can pull a model)
      #   nix build .#vm-sealed                          (sealed: nixpkgs-only egress)
      packages.${system} = {
        vm        = self.nixosConfigurations.agentos.config.system.build.vm;
        vm-sealed = self.nixosConfigurations.agentos-sealed.config.system.build.vm;
        # Boot-sanity the open variant in a VM before it touches the Dell:
        #   nix build .#vm-open && ./result/bin/run-*-vm
        vm-open   = self.nixosConfigurations.agentos-open.config.system.build.vm;

        # seal-failloud fail-down loop, driven headless (nixosTest). Kept OUT of `checks`
        # so routine `nix flake check` stays fast — this boots a VM (minutes) vs the 6
        # property batteries (seconds). Run on demand:  nix build .#test-seal-faildown
        # ENFORCED by .github/workflows/vm-tests.yml (the slow lane) — out of `checks` no
        # longer means out of CI. See that file's header for what "on demand" used to cost.
        test-seal-faildown = import ./tests/seal-faildown.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # WP-S1 acceptance: the scoped skuid-0 egress rules verified by PACKET FATE (agent vs
        # root, :443 vs arbitrary port) against a real off-box peer. Same reason as above for
        # keeping it OUT of `checks` — it boots two VMs. Run on demand:
        #   nix build .#test-egress-uid-scope
        # This is the ONLY evidence that distinguishes "the ruleset parses" (nft-ruleset-*, a
        # parse gate) and "the table exists" (test-seal-faildown) from "the wall holds".
        # ENFORCED by .github/workflows/vm-tests.yml.
        test-egress-uid-scope = import ./tests/egress-uid-scope.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # WP-S1 increment 2: the same uid-scope question asked of the MESH accepts, over a real
        # WireGuard tunnel. Increment 1 above composes baseModules without meshWireguard, so it
        # says nothing about the two accepts the mesh adds — and the inner one is where the
        # uid-blind bug was (2fb94c6). Its leg 2 is the behavioural regression test for that fix.
        # Out of `checks` for the same reason as the others: it boots two VMs. Run on demand:
        #   nix build .#test-egress-mesh-uid-scope
        # ENFORCED by .github/workflows/vm-tests.yml. Until that lane existed (2026-08-14) this
        # said "regression test" about a file NOTHING SCHEDULED EVER RAN — a regression test that
        # does not execute documents that someone once could have caught the bug. If you are ever
        # tempted to move these back out of CI, that is the sentence to re-read.
        test-egress-mesh-uid-scope = import ./tests/egress-mesh-uid-scope.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # WP-S5: the hostname allowlist, and uid 0's withdrawn direct fetch path. This is the ONLY
        # evidence for either — nft-ruleset-sealed-s5 is blind to the allowlist (it is not in the
        # ruleset) and fetch-proxy-filter-compiled proves only that the filter CODE exists, not
        # that the policy is right.
        #
        # ENFORCED. This was written as a conditional MERGE-ORDER DEPENDENCY — the slow lane
        # (.github/workflows/vm-tests.yml) was on branch `wp-s4-slow-lane-vm-tests` awaiting a
        # gate, so whichever of the two landed second owed the other a matrix entry. That
        # condition RESOLVED at 04:14:26Z on 2026-08-14: the slow lane merged as PR #88 / 5542d91.
        # This branch is therefore the one landing second, this branch is rebased onto it, and the
        # matrix entry is in this commit. The debt is paid, not deferred — a conditional left
        # standing after its condition resolves reads to the next person as an open question when
        # it is actually an unmade change.
        #
        # Out of `checks` like its siblings: it boots two VMs. Run on demand:
        #   nix build .#test-fetch-proxy-allowlist
        test-fetch-proxy-allowlist = import ./tests/fetch-proxy-allowlist.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # ── WP-S2 / GATE #5(a): the three inputs tests/cap-sandbox-battery.sh needs ──────────
        # That battery is the BEHAVIOURAL half of the per-cap fs-confinement — it needs a live
        # system systemd and root, so it can never be a check derivation. Exposing its inputs as
        # packages means it runs against the REAL artefacts (the registry-derived policy, the
        # patchShebangs'd impl dir, the materialized registry), not against hand-written stand-ins:
        #
        #   nix build .#cap-sandbox-policy .#cap-bin .#cap-registry-json
        #   sudo tests/cap-sandbox-battery.sh bin/cap-invoke \
        #        ./result-1/bin ./result-2 ./result   # (paths per the build order above)
        #
        # ENFORCED, as of `test-cap-sandbox-confinement` below: that nixosTest boots a VM and runs
        # THIS EXACT SCRIPT as root against these same artefacts, and it is in the vm-tests.yml
        # matrix. The packages here remain the at-a-box affordance (running the battery on a real
        # host, e.g. the Dell after a rebuild) — they are no longer the only way it ever executes.
        cap-sandbox-policy = nixpkgs.legacyPackages.${system}.writeText "agent-os-cap-sandbox.json"
          (import ./modules/cap-sandbox.nix { lib = nixpkgs.lib; }).policyJson;
        cap-bin = (import ./modules/cap-invoke-pkg.nix {
          pkgs = nixpkgs.legacyPackages.${system};
        }).capBinDir;
        cap-registry-json = nixpkgs.legacyPackages.${system}.writeText "agent-os-registry.json"
          (builtins.toJSON (import ./modules/capability-registry.nix { lib = nixpkgs.lib; }).registry);

        # The battery, in a booted VM — the SCHEDULED half of GATE #5(a)'s behavioural evidence.
        # Out of `checks` for the same reason as its siblings (it boots a VM); in the vm-tests.yml
        # matrix in the same commit that introduces it, per that file's own rule. Run on demand:
        #   nix build .#test-cap-sandbox-confinement
        test-cap-sandbox-confinement = import ./tests/cap-sandbox-confinement.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # The COMPOSED path — real broker -> real (confined) wrapper -> real impl, in a VM. The
        # third leg of GATE #5(a)'s evidence and the only RUNTIME witness that the two exports in
        # mkWrapper's `lib.optionalString confined` block actually deliver the confinement:
        #
        #   checks.cap-wrapper-pinned    build-time, wrapper TEXT   — production is WIRED to confine
        #   test-cap-sandbox-confinement runtime, pins AS ENV       — the confinement WORKS
        #   test-cap-composed-path       runtime, pins FROM WRAPPER — the wiring DELIVERS it
        #
        # It scrubs AGENT_OS_CAP_SANDBOX / AGENT_OS_SYSTEMD_RUN from its own environment and
        # asserts their absence, so everything under test must arrive from broker.nix's wrapper.
        # Out of `checks` for the same reason as its siblings (it boots a VM); added to the
        # vm-tests.yml matrix in the same commit, per that file's own rule. Run on demand:
        #   nix build .#test-cap-composed-path
        test-cap-composed-path = import ./tests/cap-composed-path.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # The watchdog is ARMED. Every test above watches a wall that keeps something out; this
        # one watches the mechanism that gets the box back when the walls are irrelevant because
        # the kernel has stopped responding (lived: 2026-08-11, five days dark).
        #
        # Out of `checks` for the usual reason — it boots a VM — but ALSO because it genuinely
        # cannot be a fast-lane check. The configured value is visible at eval time; whether
        # systemd actually OPENED a watchdog device is not. `RuntimeWatchdogUSec=2min` reads
        # identically on a box that armed one and a box that has no watchdog at all, so a
        # build-time assertion here would be a guard that permits everything. The guest is given
        # `-device i6300esb` so /sys/class/watchdog/watchdog0/state has a real answer to give.
        #
        # Verified to DISCRIMINATE before landing, not merely to pass: the same test against a
        # guest with RuntimeWatchdogSec forced to 0 fails on `state is 'inactive'`. A test whose
        # negative case was never run is the instrument-error sibling of this repo's own class.
        # Added to the vm-tests.yml matrix in the same commit, per that file's own rule.
        #   nix build .#test-watchdog-armed
        test-watchdog-armed = import ./tests/watchdog-armed.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };

        # The self-improvement loop, RUN on a booted machine. This is the only evidence in the
        # tree that `agos_cycle.main()` has ever been called by anything other than a battery
        # calling it directly: the engine shipped with a module manifest, an import guard and
        # 74 green contract checks while never once executing on any machine.
        # NOTE it composes `openModules`, not `baseModules` — the engine ships in the open
        # image only, and this is the first behavioural VM test the open lane has.
        # Out of `checks` for the same reason as its siblings: it boots a VM.
        #   nix build .#test-selfimprove-loop-runs
        test-selfimprove-loop-runs = import ./tests/selfimprove-loop-runs.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit openModules;
        };

        # Task 324 step 2, BOOT LEG. Step 1 (#140) shipped the minting unit and every gate stayed
        # green without any machine ever running it — the module parses, the package builds, the
        # battery calls ensure_boot_identities() in-process. None of that is a booting machine.
        # This was going to be verified on the Dell; it could not be, because the Dell runs
        # `agentos-open` and Geist's 2026-08-23 ruling is that identity FOLLOWS AUDIT (sovereign
        # only) — the open box has no agent-os unit at all, so it is the wrong instrument rather
        # than a broken one. Hence baseModules, and hence a VM: leg 5 REBOOTS the guest to prove
        # a real restart does not rotate the keys, which a single-process battery cannot show and
        # which fails catastrophically and silently if it ever regresses.
        # Out of `checks` for the same reason as its siblings: it boots a VM.
        #   nix build .#test-identity-boot
        test-identity-boot = import ./tests/identity-boot.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };
      };

      checks.${system} = {
        # Every .nix module PARSES — including ones nothing imports yet.
        #
        # WHY THIS IS ITS OWN CHECK: `agentos-open-imports` proves the modules that ARE
        # imported are wired in. Nothing proved an UNIMPORTED module is even syntactically
        # valid, so a broken or half-written module can sit on main indefinitely and only
        # explode for the first person who imports it. Same failure shape as a config fix
        # that never reaches the running process: green CI that never exercised the thing.
        #
        # `nix-instantiate --parse` checks syntax WITHOUT evaluating, so it needs none of
        # the module arguments a real import would demand.
        modules-parse =
          let p = nixpkgs.legacyPackages.${system}; in
          p.runCommand "modules-parse-check" { nativeBuildInputs = [ p.nix ]; } ''
            export NIX_STATE_DIR="$(mktemp -d)"
            fail=0
            for f in ${./modules}/*.nix; do
              if nix-instantiate --parse "$f" >/dev/null 2>err.txt; then
                echo "ok          $(basename "$f")"
              else
                echo "PARSE FAIL  $(basename "$f")"; sed 's/^/    /' err.txt; fail=1
              fi
            done
            [ "$fail" = 0 ] || { echo "modules-parse: a module does not parse"; exit 1; }
            echo "modules-parse: all modules parse"
            touch $out
          '';

        # `OLLAMA_THINK=off` is DECLARED, and the value the open variant declares is one the
        # brain's OWN parser turns into "no thinking" — checked by running that parser, not by
        # re-listing the tokens it accepts.
        #
        # WHY THIS IS NOT A STYLE CHECK. `_think_budget()` (agent-brain.py:223) returns None for
        # anything outside off/false/0/no|on/true/1/yes|low/medium/high, and None means "omit the
        # key, keep the model's default". So a typo — `OLLAMA_THINK = "disabled"`, `"OFF "`,
        # `"none"` — does not fail, does not warn, and silently ships thinking back ON. The
        # failure state of this setting is INDISTINGUISHABLE from never having set it, which is
        # the same shape as the sentinel that collided with a real value (PR #237) and the control
        # that was budgeted against but never set (MAX_LOADED_MODELS). Third instance on this
        # surface in one week, so it gets a gate rather than a comment.
        #
        # WHAT IS AT STAKE, measured on the Dell 2026-08-31: with thinking on, the 9B spends the
        # whole 2048-token `num_predict` budget reasoning and returns an EMPTY answer after ~7.5
        # minutes (twice, with a control arm). This value is not a speed tuning; it is what makes
        # the brain answer at all. A silent revert to the default is a mute box.
        #
        # BOTH HALVES ARE READ FROM THE SHIPPED CONFIG, neither retyped: the value comes out of
        # the open variant's own `environment.variables`, and the parser is the same
        # `agent-brain.py` that genesis-open.nix copies to `bin/agent-brain`. A check carrying its
        # own copy of either one stops testing the shipped thing the moment they drift.
        # THINKING IS OFF IN THE BRAIN THE SYSTEM SHIPS, WITH NO ENVIRONMENT AT ALL.
        #
        # This REPLACES `think-off-is-declared-and-parseable`, which was green throughout the
        # failure it existed to prevent. That check read OLLAMA_THINK out of
        # `environment.variables` and fed it to the parser in `agent-brain.py`, and both halves
        # were correct — but `environment.variables` builds the LOGIN environment, and the TUI
        # is started by `systemd --user` (brain-home.service, baac2a3), which never sources it.
        # So the declaration was right, the parser was right, and the running brain still had
        # OLLAMA_THINK unset and thought for 81 seconds a turn. Dillon photographed it.
        #
        # THE FIX TO THE CHECK IS THE VANTAGE, NOT THE ASSERTION. It now execs the BUILT
        # artifact (system.build.agentBrain — the script with @THINK_DEFAULT@ substituted) with
        # OLLAMA_THINK **deleted from the environment**, because that is precisely the condition
        # a systemd user unit presents. Passing the value in was the whole defect: a check that
        # supplies the input it is verifying arrives can only ever confirm its own argument.
        think-off-reaches-the-brain-with-no-env =
          let
            p = nixpkgs.legacyPackages.${system};
            brain = self.nixosConfigurations.agentos-open.config.system.build.agentBrain;
          in p.runCommand "think-off-reaches-the-brain-with-no-env" {
            nativeBuildInputs = [ p.python3 ];
          } ''
            cat > check.py <<'PYEOF'
            import importlib.util, os, sys
            from importlib.machinery import SourceFileLoader

            def think(value):
                """Load the BUILT brain under a given OLLAMA_THINK and read the constant it
                computed. `value is None` means the variable is ABSENT, not empty."""
                os.environ.pop("OLLAMA_THINK", None)
                if value is not None:
                    os.environ["OLLAMA_THINK"] = value
                # An EXPLICIT loader, because the shipped artifact is `bin/agent-brain` with no
                # .py suffix: spec_from_file_location infers the loader from the EXTENSION and
                # returns None for this path, which is how the first run of this check died.
                # Naming the loader is also the point -- the file under test is the installed
                # one, not a .py copy that would have imported by luck.
                loader = SourceFileLoader("ab", sys.argv[1])
                spec = importlib.util.spec_from_loader("ab", loader)
                m = importlib.util.module_from_spec(spec)
                loader.exec_module(m)
                return m

            # THE ARM THAT MATTERS, and the one the old check could not express: NO env.
            m = think(None)
            print("no OLLAMA_THINK in env -> THINK=%r (want False), baked default=%r"
                  % (m.THINK, m._THINK_BUILD_DEFAULT))
            if m.THINK is not False:
                raise SystemExit(
                    "the brain this system ships thinks by default when launched with no "
                    "OLLAMA_THINK (THINK=%r). That is how systemd --user starts it, so this is "
                    "the live TUI's condition, not a corner case." % (m.THINK,))

            # The substitution actually HAPPENED. Without this the check passes on a build where
            # @THINK_DEFAULT@ was never replaced but some other path produced False -- and a
            # placeholder that reaches runtime is a defect even when the answer is right today.
            if m._THINK_BUILD_DEFAULT.startswith("@"):
                raise SystemExit("@THINK_DEFAULT@ was never substituted: the shipped script "
                                 "carries the literal placeholder, so the value is accidental.")

            # CONTROL, named in the OUTPUT so a reader is not asked to take the source's word:
            # without it, a brain hard-wired to False would pass every arm above and the
            # rollback documented in configuration-open.nix would be fiction.
            on = think("on")
            print("CONTROL, not a defect: OLLAMA_THINK=on -> THINK=%r (want True) "
                  "-- proves the env still overrides, i.e. the zero-rebuild rollback is real"
                  % (on.THINK,))
            if on.THINK is not True:
                raise SystemExit("control arm failed: the env no longer overrides the baked "
                                 "default (got %r), so there is no rollback without a rebuild"
                                 % (on.THINK,))

            # CONTROL: a typo must still be distinguishable from the shipped value. It now falls
            # back to the BAKED default rather than to the model's, which is a strictly safer
            # failure -- assert that, rather than the old None.
            typo = think("disabled")
            print("CONTROL, not a defect: OLLAMA_THINK=disabled -> THINK=%r (want False via the "
                  "baked default) -- an unrecognised value no longer silently restores thinking"
                  % (typo.THINK,))
            if typo.THINK is not False:
                raise SystemExit("control arm failed: an unrecognised value parsed to %r"
                                 % (typo.THINK,))
            PYEOF
            sed -i 's/^            //' check.py
            python3 check.py ${brain}/bin/agent-brain
            echo "think-off-reaches-the-brain-with-no-env: the shipped brain does not think when launched with an empty environment"
            touch $out
          '';

        # Every module the shipped brain IMPORTS resolves FROM THE SHIPPED CLOSURE.
        #
        # WHY THIS EXISTS. #243 merged 11/11 green and shipped broken. `spend-ceiling-contract`
        # above copies modules/spend_ceiling.py into a work tree it builds itself and runs the
        # battery there — so it verified the Python and could not observe the package. The
        # derivation copied exactly one file, agent-brain.py. The gate code reached the Dell;
        # the module it imports did not. `import spend_ceiling` is wrapped in try/except by
        # design, so nothing logged: with no ceiling configured the stage is inert, and with one
        # configured it fail-closes to "spend ceiling UNAVAILABLE" — meaning the budget cannot
        # be armed AT ALL, discovered on the day a credential lands. Same vantage error as the
        # firewall outage: an instrument that cannot see the state it exists to detect.
        #
        # So this check imports from the artifact and ASSERTS THE RESOLVED PATH IS INSIDE IT.
        # Without that assertion the check passes off the checker's own cwd or a PYTHONPATH
        # leak — it would confirm the module exists somewhere, which was never in doubt.
        brain-imports-resolve-in-the-shipped-closure =
          let
            p = nixpkgs.legacyPackages.${system};
            brain = self.nixosConfigurations.agentos-open.config.system.build.agentBrain;
          in p.runCommand "brain-imports-resolve-in-the-shipped-closure" {
            nativeBuildInputs = [ p.python3 ];
          } ''
            cat > check.py <<'PYEOF'
            import os, re, subprocess, sys

            bindir  = sys.argv[1]
            store   = os.path.dirname(bindir)         # the $out of the agent-brain derivation
            repomod = sys.argv[2]                     # the REPO's modules/ dir, in the store
            src     = open(os.path.join(bindir, "agent-brain")).read()

            # Read the imports OUT OF THE SHIPPED SCRIPT rather than listing them here. A
            # hand-kept list is a second place to update, and forgetting to extend it yields a
            # silently narrower check that still prints green — the failure this file is about.
            #
            # BOTH import forms, because the two that were missing used one each: `import
            # spend_ceiling as _spend` and `from providers import load_providers as ...`. The
            # first draft of this check matched only the former and would have shipped the
            # providers half of the same bug.
            named = set(re.findall(r"^\s*import (\w+)(?: as \w+)?\s*$", src, re.M))
            named |= set(re.findall(r"^\s*from (\w+) import ", src, re.M))

            # Keep only FIRST-PARTY modules. The repo's modules/ dir is the authority on what is
            # ours — asking the package would be circular, since a module missing from the
            # package is precisely the defect. stdlib names drop out here; without this the
            # in-closure assertion below would fire on `os` and the check would be unusable.
            ours   = {f[:-3] for f in os.listdir(repomod) if f.endswith(".py")}
            wanted = sorted(named & ours)
            if not wanted:
                raise SystemExit("the shipped brain imports no first-party module — this check "
                                 "has lost its subject and would pass vacuously.")
            print("first-party imports declared by the shipped brain: %s" % (wanted,))

            # THE ARTIFACT'S OWN INTERPRETER, read out of its shebang — not this check's python3.
            # They are different: the brain ships `brainPython`, which carries pyyaml, and
            # providers.py imports yaml at module scope. Probing with the checker's bare python3
            # reported a ModuleNotFoundError that the running brain does not have, i.e. it would
            # have failed the build over a condition that exists only inside the check. A gate
            # is only worth its vantage, and the vantage here is the shipped shebang.
            interp = src.splitlines()[0].lstrip("#!").strip()
            if not interp.startswith("/nix/store/"):
                raise SystemExit("the shipped brain's shebang is %r — not a pinned store "
                                 "interpreter, so this check cannot reproduce its import "
                                 "environment." % (interp,))
            print("probing with the artifact's own interpreter: %s" % (interp,))

            # sys.path[0] is the SCRIPT'S directory for a real invocation, so reproduce exactly
            # that and nothing else. An inherited PYTHONPATH would be the leak this guards.
            env = dict(os.environ); env.pop("PYTHONPATH", None)
            probe = ("import importlib,sys,json;"
                     "print(json.dumps({m: getattr(importlib.import_module(m),'__file__',None)"
                     " for m in %r}))" % (wanted,))
            out = subprocess.run([interp, "-c", probe], cwd=bindir, env=env,
                                 capture_output=True, text=True)
            if out.returncode != 0:
                raise SystemExit("a module the shipped brain imports is NOT in the closure:\n"
                                 + out.stderr.strip())
            import json
            for mod, path in sorted(json.loads(out.stdout).items()):
                inside = bool(path) and os.path.realpath(path).startswith(os.path.realpath(store))
                print("  %-16s -> %s  in_closure=%s" % (mod, path, inside))
                if not inside:
                    raise SystemExit(
                        "%s resolved to %r, which is OUTSIDE the shipped closure %s. The check "
                        "would have passed on a package that does not contain it." % (mod, path, store))

            # The CLI the ceiling's own error text instructs the operator to run. Advice naming
            # an absent command is how "fail-closed" becomes "permanently disabled".
            cli = os.path.join(bindir, "agent-os-budget")
            if not os.access(cli, os.X_OK):
                raise SystemExit("bin/agent-os-budget is not in the closure, but spend_ceiling.py "
                                 "tells the operator to run it by name when the counter is missing.")
            print("  agent-os-budget  -> %s  executable=True" % (cli,))
            PYEOF
            sed -i 's/^            //' check.py
            python3 check.py ${brain}/bin ${./modules}
            echo "brain-imports-resolve-in-the-shipped-closure: every module the brain imports is IN the package, not merely in the repo"
            touch $out
          '';

        # ARM G — THE HAND IS BAKED IN THE BUILT ARTIFACT, not merely in the nix source.
        #
        # #282 replaces `@SH@` in agent-brain.py with the store path of bash at build time, via
        # a `--replace-fail` pair in genesis-open.nix. The battery's arm F asserts that pair is
        # PRESENT in the nix source — and geist showed F is not a detector. Comment the line out
        # rather than deleting it:
        #
        #     --replace-fail '@THINK_DEFAULT@' '${thinkDefault}' \
        #     # --replace-fail '@SH@'            '${pkgs.bash}/bin/bash'
        #
        # the `\` continuation carries into a word beginning with `#`, the shell ends the command
        # there, substituteInPlace runs with every pair BUT @SH@, and the build SUCCEEDS. The
        # built brain then carries SH_BUILD = "@SH@", falls back to resolving `sh` by name — the
        # exact defect #282 exists to remove — and F still prints "found the --replace-fail line"
        # with 0 failures. F is a word test on a file that is not the guarded thing.
        #
        # AND NOTHING DOWNSTREAM CAN SEE IT. Main carries #277's runtime fallback, so a brain
        # built with @SH@ unsubstituted still shells out correctly through the literal path: the
        # verb battery passes, the readiness receipt passes, the Dell behaves. The substitution
        # can drop out silently and stay dropped. That is why this is a gate condition and not a
        # follow-up — G is the only detector that can exist for it.
        sh-is-baked-into-the-built-brain =
          let
            p = nixpkgs.legacyPackages.${system};
            brain = self.nixosConfigurations.agentos-open.config.system.build.agentBrain;
          in p.runCommand "sh-is-baked-into-the-built-brain" {
            nativeBuildInputs = [ p.python3 ];
          } ''
            cat > check.py <<'PYEOF'
            import os, re, sys

            script = os.path.join(sys.argv[1], "agent-brain")
            src = open(script).read()

            # VACUITY GUARD FIRST. Every assertion below is about the VALUE bound to SH_BUILD, so
            # a rename or a deletion of that binding would leave this check asserting nothing over
            # nothing and still printing green — the failure mode this whole arm was written to
            # answer. Find the binding before judging it.
            m = re.search(r'^SH_BUILD\s*=\s*"([^"]*)"', src, re.M)
            if not m:
                raise SystemExit("no `SH_BUILD = \"...\"` binding in the shipped brain. Either the "
                                 "name changed or the line is gone; this check has lost its "
                                 "subject and must not pass by finding nothing to object to.")
            baked = m.group(1)
            print("SH_BUILD as shipped: %r" % (baked,))

            # (1) the placeholder is GONE from the artifact. Checked over the whole file, not just
            # the binding: @SH@ surviving anywhere in the shipped script means substituteInPlace
            # did not do its job, wherever it landed.
            if "@SH@" in src:
                raise SystemExit(
                    "the placeholder @SH@ is still present in the BUILT brain at %s. The "
                    "--replace-fail pair did not run -- and note the build SUCCEEDED anyway, "
                    "which is precisely the silent shape this arm exists to catch." % (script,))

            # (2) the baked value is a real path in the closure. Absence of the placeholder is not
            # presence of a working hand: a substitution to a typo'd or garbage-collected path
            # would clear (1) and still leave the brain unable to run a command.
            if not baked.startswith("/nix/store/"):
                raise SystemExit("SH_BUILD is %r, which is not a store path. The hand must be "
                                 "resolved at BUILD time to a pinned interpreter, not left as a "
                                 "name for the runtime to look up -- that lookup is the defect." % (baked,))
            if not os.path.exists(baked):
                raise SystemExit("SH_BUILD is %r, which does not exist. The substitution ran but "
                                 "produced a path nothing can execute." % (baked,))
            if not os.access(baked, os.X_OK):
                raise SystemExit("SH_BUILD is %r, which exists but is not executable." % (baked,))
            print("baked hand: %s  exists=True executable=True" % (baked,))
            PYEOF
            sed -i 's/^            //' check.py
            python3 check.py ${brain}/bin
            echo "sh-is-baked-into-the-built-brain: the ARTIFACT carries a pinned interpreter, not a placeholder"
            touch $out
          '';

        # The Hyprland config the OPEN variant actually ships PARSES, checked by the
        # PINNED COMPOSITOR ITSELF — not by a regex that encodes my belief about the grammar.
        #
        # WHY THIS EXISTS. `modules/desktop-open.nix` shipped eight `windowrule` lines that
        # Hyprland 0.56 rejects (`invalid field type class`). Nothing caught it: the module
        # parses as Nix, the store file is written, the system builds and switches green, and
        # the FIRST report is an error spew on the boot screen of the machine a stranger sees.
        # Every gate we had answered a question about Nix; none answered "does the compositor
        # accept this?" A config that never reaches a parser is the same shape as a fix that
        # never reaches the running process.
        #
        # THE ORACLE IS THE PIN. `pkgs.hyprland` here is store path
        # xhmd6b61rnyp4061njm8cgi9d7z115jn-hyprland-0.56.0, byte-identical to the binary on
        # the Dell — verified 2026-08-30. So this is not "a hyprland", it is THE hyprland,
        # and its grammar cannot drift from the deployed one without the pin moving.
        #
        # THE INPUT IS THE ARTIFACT, NOT A COPY. It reads the store path out of the open
        # variant's own tmpfiles rule, so the bytes checked here are the bytes symlinked to
        # ~agent/.config/hypr/hyprland.conf. Re-deriving the text would put reader and writer
        # in two languages with nothing asserting they agree, which is a scar this lane already
        # carries; there is no second spelling to drift.
        #
        # NON-ROOT IS LOAD-BEARING, not hygiene. `Hyprland --verify-config` REFUSES to run
        # with superuser privileges ("the privileges check is not omitted") and exits non-zero
        # — which a naive check would read as "config is broken" for a config that is fine,
        # or, worse, a `|| true` would launder into green. Nix builds already run as an
        # unprivileged nixbld user, so this holds by construction here; the assertion below
        # makes the dependency explicit rather than lucky.
        #
        # WHAT THIS ORACLE DOES NOT COVER, stated so a green is not over-read:
        #  - `focus({workspace})` is ABSENT from the parser's own accepted-key message and yet
        #    works at runtime (observed on the Dell, 2026-08-30, with a discriminating control:
        #    the live workspace walked 1->3->5). The parse message is not the contract, so a
        #    key missing from it is not evidence the key is dead — and conversely, a key it
        #    accepts is not evidence the dispatcher does anything.
        #  - `--verify-config` EXECUTES the config's `exec` lines (observed, same session). It
        #    is a parser AND an interpreter. Startup commands therefore run inside the build
        #    sandbox, where they will mostly fail; keep them poking units rather than doing
        #    work, and never assume this check is side-effect-free.
        #
        # ARMED RED BEFORE IT WAS TRUSTED. Run against the eight broken rules on main it
        # FAILED, naming lines 68-72/77/78/80 — that is the pre-fix arm, and without it a
        # check that verified nothing would have shipped looking identical to this one.
        # The Tailscale SSH re-assert HEALS THE OFF STATE — armed against the exact
        # input that defeated it in production, running the SHIPPED binary.
        #
        # WHY THIS EXISTS. The unit went out with `jq -r '.RunSSH // "MISSING"'`. jq's
        # alternative operator fires on `false` as well as `null`, so `RunSSH=false` —
        # the ONE state the healer exists to correct — came back as the MISSING sentinel
        # and left through the CANNOT-ASSESS arm. Measured on the Dell 2026-08-31: after
        # a hand `tailscale set --ssh=false`, the timer fired on its own at 03:49:46 CDT
        # and journalled "CANNOT-ASSESS — .RunSSH was 'MISSING'", exit 2, unit `failed`,
        # pref still false. Every earlier run was green because the pref was true — the
        # healer was reachable ONLY in the branch where there was nothing to heal, and a
        # deploy, a review and a passing unit all agreed with it.
        #
        # THE ARM IS THE FALSE CASE. A check that only fed it `true` would pass against
        # the broken binary and the fixed one alike. The false arm is the whole check;
        # the other two exist so a binary that answered HEAL to everything cannot pass.
        #
        # IT RUNS THE SHIPPED DERIVATION, not a copy of the logic. `system.build.
        # agosTailscaleSshReassert` is the same store path the module puts in
        # systemPackages, driven against a stub `tailscale` earlier on PATH. Re-deriving
        # the jq program here would put the check and the unit in two spellings with
        # nothing asserting they agree — the drift this surface keeps scarring on.
        tailscale-ssh-reassert-heals-off-state =
          let
            p = nixpkgs.legacyPackages.${system};
            # A stand-in `tailscale` that reports whatever prefs the arm asks for and
            # records any `set` call. It is injected as `services.tailscale.package`,
            # the very attribute the module already reads — NOT prepended to PATH:
            # writeShellApplication exports its runtimeInputs AHEAD of $PATH, so a PATH
            # stub is silently outranked by the real binary. (Observed here: arm 1 came
            # back "Failed to connect to local Tailscale daemon" from the genuine CLI.)
            stubTailscale = p.writeShellScriptBin "tailscale" ''
              if [ "$1" = "debug" ]; then cat "$AGOS_TEST_PREFS"; exit 0; fi
              if [ "$1" = "set" ]; then echo "SET $*" >> "$AGOS_TEST_LOG"; exit 0; fi
              exit 9
            '';
            bin = (self.nixosConfigurations.agentos-open.extendModules {
              modules = [ { services.tailscale.package = p.lib.mkForce stubTailscale; } ];
            }).config.system.build.agosTailscaleSshReassert;
          in p.runCommand "tailscale-ssh-reassert-heals-off-state" { } ''
            export AGOS_TEST_PREFS=$(mktemp) AGOS_TEST_LOG=$(mktemp)
            # NOT `out=$(run)`: `$out` is the derivation's own output path and shadowing
            # it makes the final `touch $out` write to the captured message instead.
            run() { ${bin}/bin/agos-tailscale-ssh-reassert; }
            fail() { echo "tailscale-ssh-reassert: $1"; exit 1; }

            # ARM 1 — THE REGRESSION ARM. RunSSH=false must heal (exit 0) and must have
            # actually called `tailscale set --ssh=true`. The pre-fix binary exits 2.
            printf '%s' '{"RunSSH":false}' > "$AGOS_TEST_PREFS"; : > "$AGOS_TEST_LOG"
            set +e; res=$(run 2>&1); rc=$?; set -e
            printf 'arm1 REGRESSION-ARM (RunSSH=false MUST heal) rc=%s EXPECT=0 %s\n' "$rc" "$res"
            [ "$rc" = 0 ] || fail "RunSSH=false did not heal (rc=$rc): $res"
            grep -q -- '--ssh=true' "$AGOS_TEST_LOG" \
              || fail "RunSSH=false reported healed without calling set --ssh=true"

            # ARM 2 — CONTROL. Its row says so in the OUTPUT, not just here: on 2026-08-31 a
            # digest read this arm's bare `arm2 rc=0` line as a reported defect and broadcast
            # "deploy claim needed" to six brains before being corrected. A control arm that
            # only announces itself in a source comment is indistinguishable from a finding to
            # everything downstream that reads the log. Already serving: exit 0 and NO set call. Without this, a
            # binary that blindly set on every run would pass arm 1.
            printf '%s' '{"RunSSH":true}' > "$AGOS_TEST_PREFS"; : > "$AGOS_TEST_LOG"
            set +e; res=$(run 2>&1); rc=$?; set -e
            printf 'arm2 CONTROL, not a defect (RunSSH=true MUST stay silent) rc=%s EXPECT=0 %s\n' "$rc" "$res"
            [ "$rc" = 0 ] || fail "RunSSH=true should be a silent pass (rc=$rc)"
            [ ! -s "$AGOS_TEST_LOG" ] || fail "RunSSH=true called set anyway: $(cat "$AGOS_TEST_LOG")"

            # ARM 3 — control. A genuinely ABSENT field is CANNOT-ASSESS (2), which is
            # what the sentinel was always for. This is what distinguishes the fix from
            # simply deleting the sentinel and calling every non-true value false.
            printf '%s' '{"WantRunning":true}' > "$AGOS_TEST_PREFS"
            set +e; res=$(run 2>&1); rc=$?; set -e
            printf 'arm3 CONTROL, not a defect (absent field MUST cannot-assess) rc=%s EXPECT=2 %s\n' "$rc" "$res"
            [ "$rc" = 2 ] || fail "absent RunSSH should be CANNOT-ASSESS 2, got $rc"

            echo "tailscale-ssh-reassert-heals-off-state: false heals, true is silent, absent cannot-assess"
            touch $out
          '';

        hyprland-config-parses =
          let
            p = nixpkgs.legacyPackages.${system};
            conf = self.nixosConfigurations.agentos-open.config.system.build.hyprlandConf;
          in p.runCommand "hyprland-config-parses" {
            nativeBuildInputs = [ p.hyprland ];
          } ''
            # Hyprland refuses to run as root; a build runs as nixbld, so assert that
            # rather than assume it. If this ever fires, the check below is meaningless.
            if [ "$(id -u)" = 0 ]; then
              echo "hyprland-config-parses: running as root — --verify-config would refuse"
              echo "and its non-zero exit would be indistinguishable from a broken config."
              exit 1
            fi
            export XDG_RUNTIME_DIR="$(mktemp -d)"
            export HOME="$(mktemp -d)"

            echo "oracle: $(Hyprland --version 2>&1 | head -1)"
            echo "config: ${conf}"

            # --verify-config parses and exits; it never opens a display. Capture BOTH
            # streams: 0.56 prints config errors to stdout and the summary to stderr, so
            # a check reading only one of them can miss the whole finding.
            set +e
            verify="$(Hyprland --verify-config -c ${conf} 2>&1)"
            rc=$?
            set -e
            printf '%s\n' "$verify"

            # rc alone is NOT sufficient and that is the point: 0.56 exits 0 while printing
            # `invalid field ...` for every rejected line, so a check keyed on the exit code
            # is green on the exact config that spews errors at boot. Key on the errors.
            if printf '%s' "$verify" | grep -qiE 'invalid field|error|deprecated'; then
              echo ""
              echo "hyprland-config-parses: the PINNED compositor rejects this config."
              echo "Fix the config — do not relax this grep."
              exit 1
            fi
            if [ "$rc" != 0 ]; then
              echo "hyprland-config-parses: --verify-config exited $rc"
              exit 1
            fi
            echo "hyprland-config-parses: pinned Hyprland 0.56 accepts the open variant config"
            touch $out
          '';

        # Phase 2 · Step 1 — evaluating the capability registry FORCES its invariant
        # assertions (mechanism 3 + INV-2 + schema). Any violation throws during eval,
        # so `nix flake check` fails to build this. That failure IS the test — a
        # configuration that breaks a security invariant does not evaluate.
        capability-registry =
          let reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
          in nixpkgs.legacyPackages.${system}.runCommand "capability-registry-check" { } ''
            echo "capability registry invariants hold (ok=${builtins.toJSON reg.ok})"
            echo "capabilities: ${nixpkgs.lib.concatStringsSep " " reg.capabilityNames}"
            touch $out
          '';

        # WP-C3 — the boot label actually carries the CONFIG rev, on EVERY variant.
        #
        # ADDED AFTER THE FACT, and the reason is the lesson task 324 step 1 just taught at cost:
        # `revModule` was correct and nothing asserted that it REACHED anything. Worse, when I
        # first control-armed this change I injected a throw into `revModule` and watched the
        # `modules-parse` check stay GREEN — that check does not consume `revModule`, so the
        # fixture never reached the code under test and a green sweep would have been reported as
        # control-armed while the control was inert. A negative result from an instrument not
        # wired to the thing under test proves nothing.
        #
        # So this asserts the SURFACE a human reads (`system.nixos.label`), not the input, and on
        # all four variants rather than the one that happens to be checked. Eval-only: zero
        # realization, no model FOD, DVo-cheap. Drop the tag from revModule and this throws.
        #
        # It asserts the `cfg-` PREFIX, not merely that the rev appears. Two hashes on one line
        # with one of them unlabelled is precisely what got misread on 2026-08-08, so the prefix
        # IS the change and an assertion that ignored it would pass a regression that undid it.
        boot-label-carries-config-rev =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            lib  = nixpkgs.lib;
            rev  = self.shortRev or self.dirtyShortRev or "dirty";
            want = "cfg-" + rev;
            labelOf = n: self.nixosConfigurations.${n}.config.system.nixos.label;
            variants = [ "agentos" "agentos-sealed" "agentos-sealed-s5" "agentos-open" ];
            bad = lib.filter (n: !(lib.hasInfix want (labelOf n))) variants;
          in
            assert lib.assertMsg (bad == [ ])
              ("boot-label-carries-config-rev: these variants do NOT carry `${want}` in system.nixos.label: "
               + lib.concatStringsSep " " bad
               + ". The boot menu would show only the nixpkgs pin, which READS like an answer to "
               + "\"what config is this box running?\" and is an answer to a different question.");
            pkgs.runCommand "boot-label-check" { } ''
              echo "all four variants carry ${want} in system.nixos.label:"
              ${lib.concatStringsSep "\n" (map (n: "  echo '  ${n}: ${labelOf n}'") variants)}
              touch $out
            '';

        # Phase 1.5B · task 324 step 1 — the first-boot identity WIRING battery. The layer
        # itself is covered by tests/identity-battery.py; this one covers what was actually
        # broken, which is that nothing called it and nothing agreed where the keys live. Its
        # section C reads the nix files, so an author who removes the import or unpins the root
        # fails `nix flake check` rather than shipping a box that silently cannot sign.
        identity-boot =
          nixpkgs.legacyPackages.${system}.runCommand "identity-boot-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              cp -r ${./.}/. src && chmod -R u+w src
              python3 src/tests/identity-boot-battery.py
              touch $out
            '';

        # Phase 1.5B · ruling condition 3 — the INTERIM-SIGNER EXPOSURE TRIPWIRE. modules/bip340.py
        # opens by stating its own binding condition in prose: "INTERIM SIGNER — MUST be replaced by
        # libsecp256k1 (Path B) before any network exposure of these keys." Geist's 2026-08-19 Path-A
        # ruling rests entirely on that clause — the vendored reference implementation is not timing
        # hardened, and that is ACCEPTABLE only while a local same-UID adversary (who can just read the
        # key file, a strictly stronger capability) is the whole threat model. The day a remote party
        # can invoke and time this signer, the argument inverts and Path B becomes mandatory.
        #
        # Until now that condition was enforced by the paragraph and by nobody reading it. This check
        # computes the transitive set of modules that can REACH bip340 — statically AND through
        # `importlib.import_module`, which is not optional here: bin/audit reaches both `identity` and
        # `bip340` that way (bin/audit:114) and appears in NO static import grep, so the naive
        # extractor is blind to the one caller holding the signing key. Two distinct reds: EXPOSURE (a
        # network-facing module can reach the signer — the condition is violated) and ALLOWLIST DRIFT
        # (a new local caller — not a violation, but a widening someone should clear deliberately).
        # Green today (identity, audit); red the day a network-facing module imports it.
        #
        # The selftest runs in the same derivation ON PURPOSE — 8 arms, 3 of them controls: A2 pins
        # that a healthy tree is GREEN (without it a checker that fails on everything passes), A5 that
        # `urllib.parse` is string surgery and not network I/O (without it the check is noise and gets
        # uninstalled before the day it is right), and A6 runs the naive static-only extractor against
        # the importlib edge and asserts it MISSES, so A3 is shown catching what a plausible
        # implementation does not. A4 is the vacuity arm: no sources discovered must FAIL, not pass.
        bip340-exposure =
          nixpkgs.legacyPackages.${system}.runCommand "bip340-exposure-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              cp -r ${./.}/. src && chmod -R u+w src
              cd src
              python3 tests/bip340-exposure-selftest.py
              python3 tests/bip340-exposure-contract.py
              touch $out
            '';

        # pr-currency-selftest -- 18 arms, 6 of them controls.
        #
        # It ran BY HAND ONLY until 2026-09-05, and that is the exact shape the tool it tests was
        # written about: a battery nothing executes is prose with a shell prompt, and its green is a
        # claim about the day someone last typed the command. The blocker was real, not neglect --
        # the gate-strength and paths arms read the surrounding checkout via `git rev-parse HEAD~6`,
        # so they could not run in a sandbox with no history, AND their inputs changed on every push
        # here, so a green was never reproducible twice. Both are fixed by a synthetic fixture repo
        # the arms build themselves; FX2 is what licenses this wiring, asserting the battery gives
        # the same answer from a cwd that is not a git repo at all.
        #
        # `gh` is deliberately NOT an input: every arm that needs it uses a stub on PATH, so a
        # derivation that could reach GitHub would be testing the network rather than the parser.
        pr-currency-selftest =
          nixpkgs.legacyPackages.${system}.runCommand "pr-currency-selftest"
            {
              nativeBuildInputs = [
                nixpkgs.legacyPackages.${system}.git
                nixpkgs.legacyPackages.${system}.gawk
              ];
            } ''
              cp -r ${./.}/. src && chmod -R u+w src
              cd src
              export HOME=$TMPDIR
              bash tools/pr-currency.sh --selftest
              touch $out
            '';

        # bip340-contract -- ruling condition 2, and this one is NOT routine debt repayment.
        #
        # tests/bip340-battery.py opens by stating, as settled fact:
        #
        #     Binding condition 2 of Geist's 2026-08-19 Path-A ruling: the FULL official
        #     test-vector set runs in CI, INCLUDING the must-fail verification vectors,
        #     control-armed.
        #
        # It did not run in CI. Not via flake.nix, not via any workflow step. It has been on
        # KNOWN_UNWIRED_DEBT the whole time, so the repo simultaneously recorded "this runs in
        # CI" in the file's own header and "this runs nowhere" in the ledger, and neither
        # statement ever had to meet the other.
        #
        # THE CLASS: A RULING CONDITION THAT IS DISCHARGED BY WRITING A FILE IS DISCHARGED BY
        # PROSE. What condition 2 actually demands is an EXECUTION, and the only evidence that
        # an execution happens is a lane that goes red when it stops.
        #
        # The must-fail half is why this is worth flagging rather than quietly fixing. A
        # verifier returning True unconditionally passes every TRUE vector; vectors 5-15 and
        # the control arm at check I are what catch it. So the unrun battery was specifically
        # the forgery-acceptance coverage, which is the half the ruling singled out.
        #
        # Pure stdlib, reads its vectors from a committed CSV, and hard-fails if that CSV is
        # absent (csv.DictReader on a missing path raises) -- so it cannot go vacuously green
        # the way the self-disarming agos-* batteries would.
        bip340-contract =
          nixpkgs.legacyPackages.${system}.runCommand "bip340-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/tests" "$work/modules"
              cp ${./tests/bip340-battery.py} "$work/tests/bip340-battery.py"
              cp ${./tests/bip340-test-vectors.csv} "$work/tests/bip340-test-vectors.csv"
              cp ${./modules/bip340.py} "$work/modules/bip340.py"
              cd "$work"
              python3 tests/bip340-battery.py
              touch $out
            '';

        # The login shell's brain-precedence arms. agent-shell is the login program:
        # its failure mode is a box that will not boot, and until 2026-09-02 it was the
        # only component in the tree with no arm on it. Proves the cloud brain is never
        # auto-selected (installing Claude Code must not silently invert "nothing leaves
        # the machine") and that NO brain is exec'd without a passing probe (a `claude`
        # present on PATH but unrunnable — the NixOS dynamic-ELF case — must fall to the
        # local chain, not crash-loop getty's autologin). It drives the REAL script in a
        # sandboxed HOME with stub brains, because the defect lives in agent-shell's own
        # selection branch. A regression here fails `nix flake check`.
        agent-shell-brain-precedence =
          nixpkgs.legacyPackages.${system}.runCommand "agent-shell-brain-precedence-check"
            {
              nativeBuildInputs = with nixpkgs.legacyPackages.${system}; [ bash coreutils gnugrep ];
            } ''
              work="$(mktemp -d)"
              mkdir -p "$work/bin" "$work/tests"
              cp ${./bin/agent-shell} "$work/bin/agent-shell"
              cp ${./tests/agent-shell-brain-precedence.sh} "$work/tests/agent-shell-brain-precedence.sh"
              chmod +x "$work/bin/agent-shell" "$work/tests/agent-shell-brain-precedence.sh"
              cd "$work"
              bash tests/agent-shell-brain-precedence.sh
              touch $out
            '';

        # Phase 2 · Step 2 — the audit-log primitive's property battery. Proves
        # append-only NDJSON, SHA-256 chain tamper/truncation evidence, no-log->no-execute
        # fail-closed, hostile-newline single-line safety, and reserved-field forgery
        # stripping. A regression here fails `nix flake check`.
        audit-log =
          nixpkgs.legacyPackages.${system}.runCommand "audit-log-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/audit-battery.sh} ${./bin/audit} "$work"
              touch $out
            '';

        # Phase 2 · Step 3 — the taint tracker's property battery (SHADOW mode). Proves
        # the anti-laundering invariants: a monotonic set-only per-session bit, human-only
        # reset, UNTRUSTED-absorbing mem origin tags stored where the model can't write,
        # recall-of-untrusted re-taints across sessions, boot-taint on untrusted mem, and
        # fail-closed audit logging that gates NOTHING in v1. It drives the real audit
        # primitive, so a regression in either fails `nix flake check`.
        taint-shadow =
          nixpkgs.legacyPackages.${system}.runCommand "taint-shadow-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/taint-battery.sh} ${./bin/taint} ${./bin/audit} "$work"
              touch $out
            '';

        # Phase 2 · Step 4 — the MCP stdio front door's conformance + hostile-input
        # battery. Proves the parser invariants: one pinned protocol rev, three methods
        # only, and fail-closed on every unknown/malformed/oversized/duplicate-id/
        # duplicate-key/type-coerced/trailing-byte/unicode-trick input, with hard
        # size+depth caps and NO parser differential (one parser, one schema, no
        # coercion). The parser is pure (no privileged state), so this check needs no
        # scratch dirs beyond a workdir. A regression fails `nix flake check`.
        mcp-conformance =
          nixpkgs.legacyPackages.${system}.runCommand "mcp-conformance-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/mcp-battery.sh} ${./bin/mcp} "$work"
              touch $out
            '';

        # Phase 2 · Step 5 — the broker's property battery ("the wall"). Proves the 10-stage
        # fail-closed pipeline against the REAL materialized registry + the REAL taint/audit
        # primitives as children: the routing matrix (T0 ALLOW-AUTO, T1/T2 REQUIRE-CONFIRM),
        # T3 non-expressibility, per-type arg validation (path canonical+confined via shared
        # golden vectors, url INV-2 + obfuscated-IP evasion, namespace-under-root, recipient
        # charset, enum-denies-pre-GAP-1), verdict passthrough, single-flight + malformed-line
        # stream-shut, no-log->no-execute, the return-path taint-before-content ordering
        # (origin broker-derived not impl-reported, withhold-on-taint-fail, broker-owns-the-
        # stamp), the DATA fence, confirm epoch binding, and mcp-input-contract compatibility.
        # It materializes the registry with the SAME builtins.toJSON the broker module ships,
        # so a registry change that would break classification fails `nix flake check` here.
        broker-core =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "broker-core-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/broker-battery.sh} ${./bin/broker} ${./bin/taint} ${./bin/audit} \
              ${./bin/mcp} ${registryJson} "$work"
            touch $out
          '';

        # Phase 2 · Step 6 — the confirm channel ("the wall's mouth") property battery. Proves the
        # broker-owned confirm client (bin/confirm) is a fail-closed renderer/relayer/collector:
        # seam contract on both backends, no-channel/unreachable/unknown-name all deny (never
        # allow), nonce echo + domain-separated-HMAC auth (reflection/badmac/wrong-user/epoch each
        # caught), human-window + EOF timeouts, field-confusion/injection scrubbing with the `│ `
        # sentinel on every model-controlled value line while the trust region stays intact,
        # parse_mode-None transmitted frames, first-time-destination highlighting, preview
        # truncation, single-flight non-multiplexing, and a REAL-broker integration leg
        # (route->confirm->approve->invoke / route->confirm->deny->withhold) whose audit chain
        # still verifies and carries the nonce. It drives the real broker/taint/audit as children,
        # so a regression in any of them fails `nix flake check` here.
        confirm-channel =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "confirm-channel-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/confirm-battery.sh} ${./bin/confirm} ${./bin/broker} ${./bin/taint} \
              ${./bin/audit} ${registryJson} "$work"
            touch $out
          '';

        # Phase 4 (v0.2 A2) — the tool-calling loop's property battery. Drives the REAL
        # bin/agent-loop against a scripted fake Ollama (tests/ollama-stub.py) over the sandbox
        # loopback AND the REAL bin/mcp piped into a deterministic stub broker
        # (tests/broker-stub.py), and proves the loop MECHANICS: a plain answer skips tools; a
        # valid capability call round-trips THROUGH THE WALL (dispatch -> broker data_result fed
        # back as a role:"tool" message that preserves the content_type:"data" envelope -> final
        # answer), with the tool surface itself discovered through the wall via capabilities.list;
        # three broker denials in one user turn stop tool-calling and the final turn is taken with
        # NO tools offered; tool_calls emitted on that withheld final turn are NEVER dispatched;
        # and model-supplied terminal control bytes are scrubbed before the tty. agent-loop lives
        # on the UNTRUSTED side and makes zero security decisions (the stub replaces the broker's
        # policy decision, not the loop's behavior), so this is loop correctness, not a wall
        # policy test — mcp and broker carry their own batteries. A regression fails `nix flake check`.
        agent-loop =
          nixpkgs.legacyPackages.${system}.runCommand "agent-loop-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/agent-loop-battery.sh} ${./bin/agent-loop} ${./tests/ollama-stub.py} \
                ${./bin/mcp} ${./tests/broker-stub.py} "$work"
              touch $out
            '';

        # Phase 2 · Step 7 — the capability seam's property battery. Proves the invoke-seam
        # DISPATCHER (bin/cap-invoke) is a thin, fail-closed resolver against the REAL materialized
        # registry: capability->impl resolution, the {ok,content,meta:{key}} contract, the
        # exit-code bright line (impl exit 0 + valid object forwarded — incl. ok:false error bodies
        # that must reach the taint fence; impl crash / garbage / cap-bin-dir-unset / path-escaping
        # impl name all fail closed with no forwarded bytes), and normalization that strips any
        # impl-reported origin / extra meta down to identity. It also drives the first impl
        # (bin/cap-capabilities-list, T0). A regression in either fails `nix flake check`.
        capabilities =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "capabilities-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/cap-battery.sh} ${./bin/cap-invoke} ${./bin/cap-capabilities-list} \
              ${registryJson} "$work"
            touch $out
          '';

        # Phase 2 · A2 — the mem.* capability IMPLS' property battery. `capabilities` above proves the
        # DISPATCHER (cap-invoke); this proves the two impls BEHIND mem.remember / mem.recall via
        # DIRECT-invoke with AGENT_OS_MEM_ROOT=<scratch> — the DESIGNED test override (cap-mem-remember:41).
        # The seam strips AGENT_OS_MEM_ROOT (impl env = {PATH, AGENT_OS_REGISTRY}), so a through-seam write
        # lands at the hardcoded /var/lib/agent-os/mem, which a non-root check-derivation cannot create;
        # the direct path is the only in-sandbox home for the write round-trip. Covers PIN-2 byte-identity
        # (no trailing-newline hinge), PIN-A per-entry content-hash binding, content-addressed idempotency,
        # multi-hit score-ordering + MAX_HITS ceiling, the key-grammar fence (nothing written on an illegal
        # namespace), and fail-closed DROP of oversized / non-utf8 / illegal-slug files. The through-the-WALL
        # write->read E2E (real /var, boot-wired seam, root) is a nixosTest-VM forward-obligation on
        # task-279, deliberately NOT built here (a check-derivation cannot write /var). Regression -> RED.
        mem-cap =
          nixpkgs.legacyPackages.${system}.runCommand "mem-cap-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/mem-cap-battery.sh} ${./bin/cap-mem-remember} ${./bin/cap-mem-recall} "$work"
            touch $out
          '';

        # Phase S · WP-S2 — the file.* capability IMPLS' property battery. `capabilities` above
        # proves the DISPATCHER (cap-invoke); this proves the two impls BEHIND file.read / file.write
        # via DIRECT-invoke with AGENT_OS_FILE_SAFE_ROOT / AGENT_OS_FILE_WORKSPACE_ROOT = <scratch>
        # — the DESIGNED test override (mirrors AGENT_OS_MEM_ROOT, mem-cap above). The seam strips
        # both env vars (impl env = {PATH, AGENT_OS_REGISTRY}), so a through-seam call always uses
        # the hardcoded /var/lib/agent-os/{safe-read,workspace} roots, which a non-root check-
        # derivation cannot create; the direct path is the only in-sandbox home for the round-trip.
        # Covers write->read byte-identity, path-confinement fences (nothing written/read outside
        # the declared root), non-canonical-path rejection, symlink refusal on both read and write,
        # and arg-schema fail-closed legs. Regression -> RED.
        file-cap =
          nixpkgs.legacyPackages.${system}.runCommand "file-cap-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/file-cap-battery.sh} ${./bin/cap-file-read} ${./bin/cap-file-write} "$work"
            touch $out
          '';

        # Phase S · WP-S2 / GATE #5(a) — the EVAL half of the per-cap fs-confinement: the derived
        # systemd properties are the RIGHT ones for every capability in the registry. The
        # BEHAVIOURAL half (the kernel actually stops the escape) needs real systemd and lives in
        # tests/cap-sandbox-battery.sh; this check is what keeps the derivation honest between
        # runs of that battery, and it is deliberately narrow about what it claims.
        #
        # What it proves, per capability:
        #   * the empty-root boundary is present — `TemporaryFileSystem=/:ro` is what makes
        #     "everything else" ABSENT. ProtectSystem/ReadOnlyPaths only remove WRITE permission,
        #     so asserting those alone would assert the wrong thing (the exact confusion that made
        #     file.read fail-open in the first place);
        #   * every declared readOnlyPath is bound read-only and every readWritePath read-write;
        #   * NO declared path is bound with the wrong mode (a readOnlyPath must never appear as
        #     BindPaths=);
        #   * a non-network cap gets PrivateNetwork=yes + IPAddressDeny=any;
        #   * every registry protectedPath that the cap does not legitimately overlap is
        #     InaccessiblePaths.
        # Regression -> RED at eval time, before anything is built.
        cap-sandbox =
          let
            lib = nixpkgs.lib;
            regMod = import ./modules/capability-registry.nix { inherit lib; };
            sb = import ./modules/cap-sandbox.nix { inherit lib; };
            reg = regMod.registry;
            has = name: prop: lib.elem prop (sb.policy.${name});
            capOk = name:
              let c = reg.${name}; in
              has name "TemporaryFileSystem=/:ro"
              # ProtectSystem must NOT be set: measured on systemd 255, it re-exposes the host
              # /etc and /usr over the empty root and reopens the very read this slice closes.
              # Asserted as an ABSENCE so a well-meaning "add the standard hardening option"
              # edit fails here instead of silently un-confining file.read.
              && !(lib.any (p: lib.hasPrefix "ProtectSystem=" p) sb.policy.${name})
              && has name "NoNewPrivileges=yes"
              && lib.all (p: has name "BindReadOnlyPaths=${p}") c.sandbox.readOnlyPaths
              && lib.all (p: has name "BindPaths=${p}") c.sandbox.readWritePaths
              # mode is not confusable in either direction
              && lib.all (p: !(has name "BindPaths=${p}")) c.sandbox.readOnlyPaths
              && (c.sandbox.network
                  || (has name "PrivateNetwork=yes" && has name "IPAddressDeny=any"))
              && lib.all (p:
                   let overlaps = lib.any (d: d == p || lib.hasPrefix (d + "/") p
                                              || lib.hasPrefix (p + "/") d)
                                    (c.sandbox.readOnlyPaths ++ c.sandbox.readWritePaths);
                   in overlaps || has name "InaccessiblePaths=-${p}")
                 regMod.protectedPaths;
            bad = lib.filter (n: !(capOk n)) (lib.attrNames reg);
          in
          assert lib.assertMsg (bad == [ ])
            "cap-sandbox: derived confinement is wrong for [${lib.concatStringsSep " " bad}] — see modules/cap-sandbox.nix.";
          # file.read is the cap whose ONLY symlink boundary this is; name it explicitly so a
          # future registry edit that drops its root fails here with a legible message.
          assert lib.assertMsg
            (lib.elem "BindReadOnlyPaths=/var/lib/agent-os/safe-read" sb.policy."file.read")
            "cap-sandbox: file.read lost its safe-read bind — that bind IS its symlink boundary.";
          assert lib.assertMsg
            (lib.elem "BindPaths=/var/lib/agent-os/workspace" sb.policy."file.write")
            "cap-sandbox: file.write lost its workspace bind.";
          # NEGATIVE CONTROL for the runtimePaths guard. Everything above asserts what the DEFAULT
          # runtimePaths produces, and would stay green if the guard were deleted outright — the
          # default is legal either way. So force the other direction: each hostile override must
          # FAIL to evaluate. `tryEval` catches the assert; `deepSeq` is required because policyJson
          # is a thunk and a lazily-unforced assertion is an assertion that never fires.
          #
          # The list is not decorative. "/" is the whole host bound back over the empty root (the
          # ProtectSystem failure, reached through a parameter). "/etc" contains the protected-READ
          # broker config, so it would grant credentials.read to every cap. The last two are
          # non-canonical forms that systemd canonicalizes at unit-load, which is exactly how a
          # textual guard gets bypassed if it only checks the pretty spelling.
          assert
            let
              denies = rtp:
                let r = builtins.tryEval
                          (builtins.deepSeq
                            (import ./modules/cap-sandbox.nix { inherit lib; runtimePaths = rtp; }).policyJson
                            true);
                in !r.success;
              hostile = [ [ "/" ] [ "/etc" ] [ "/nix/store" "/var/lib/agent-os" ]
                          [ "/usr/lib/" ] [ "/nix/store/../etc" ] ];
              leaked = lib.filter (rtp: !(denies rtp)) hostile;
            in lib.assertMsg (leaked == [ ])
              ("cap-sandbox: runtimePaths guard did not reject "
               + "[${lib.concatStringsSep " | " (map (r: lib.concatStringsSep "," r) leaked)}]. "
               + "That argument is bound read-only into EVERY cap namespace, so an unguarded value "
               + "hands back what TemporaryFileSystem=/:ro took away.");
          assert
            # checkAllow NEGATIVE CONTROL, and read the scope line carefully because two revisions
            # of this comment got it wrong in the same direction. `checkAllow` inspects
            # `c.sandbox.egressAllow` -- the OPERATOR-DECLARATION path, the one registry invariant
            # (5b) deliberately leaves open by forbidding a non-empty egressAllow in-tree. It is the
            # same MECHANISM as PR #263 (a blanket Allow matches every address at rule 1 of
            # systemd.resource-control(5), so the egressDeny list is dead by construction), but it is
            # NOT the #263 INSTANCE: that one was hard-coded in `netProps` itself and never passes
            # through this filter at all. Demonstrated, not reasoned -- PR #266 reintroduced the
            # verbatim #263 shape and this control stayed GREEN (job 100733793522). The hard-coded
            # shape is caught by `hardcodedBlanket` below (eval) and by the battery's arm-8
            # precondition (VM, job 100733794512). Two guards, two defects, and the sentence that
            # used to sit here claimed one covered both. Driven through the exported `netProps` seam.
            let
              np = (import ./modules/cap-sandbox.nix { inherit lib; }).netProps;
              # The deny entry is TEST-NET-2 and the specific-allow control TEST-NET-3 (RFC 5737
              # documentation ranges) rather than an RFC1918 literal: `checkAllow` inspects only
              # egressAllow, so the deny value is inert here, and the personal-data gate is right to
              # refuse a 10/8 literal in a public repo. Removed rather than allowlist-exempted --
              # an exemption would retire this line from scrutiny for every future pattern too.
              evals = allow:
                (builtins.tryEval (builtins.deepSeq
                  (np { sandbox = { network = true; egressDeny = [ "198.51.100.0/24" ]; egressAllow = allow; }; })
                  true)).success;
              blanket = [ [ "any" ] [ "0.0.0.0/0" ] [ "::/0" ]
                          # mixed: a specific CIDR must NOT launder a blanket entry beside it
                          [ "203.0.113.4/32" "any" ] ];
              admitted = lib.filter evals blanket;
              # POSITIVE CONTROL, and it is the half that stops this arm passing vacuously: a guard
              # that threw on EVERYTHING would satisfy the check above while breaking every legitimate
              # operator exception. A specific CIDR must still evaluate, and an empty list must too.
              specificOk = evals [ "203.0.113.4/32" ];
              emptyOk = evals [ ];
            in lib.assertMsg (admitted == [ ] && specificOk && emptyOk)
              ("cap-sandbox: checkAllow negative control failed. Blanket egressAllow values that were "
               + "ADMITTED (each one reinstates the dead-deny-list defect of #263): "
               + "[${lib.concatStringsSep " | " (map (a: lib.concatStringsSep "," a) admitted)}]. "
               + "Specific-CIDR control evaluated: ${lib.boolToString specificOk}; "
               + "empty-list control evaluated: ${lib.boolToString emptyOk} "
               + "(both must be true -- a guard that refuses everything is not a guard).");
          assert
            # hardcodedBlanket EVAL CONTROL. This is the arm the checkAllow control above cannot be:
            # checkAllow filters `egressAllow`, so a blanket written directly into `netProps` --
            # which is EXACTLY what PR #263 was -- walks straight past it. PR #266 proved that
            # empirically: the verbatim #263 diff left the checkAllow control green and was caught
            # only in the VM lane, by the battery's arm-8 precondition (job 100733794512), ~6 minutes
            # into a run instead of at eval.
            #
            # The property, and it is deliberately not "no `any`": for a capability declaring NO
            # exception at all, netProps must render ZERO `IPAddressAllow=` entries of ANY value.
            # With `egressAllow = []` there is no legitimate source for an Allow entry, so any Allow
            # that appears is hard-coded in this module by construction -- which catches a narrowed
            # re-introduction (`IPAddressAllow=0.0.0.0/0`, or some future specific literal) that a
            # string match on "any" would wave through.
            let
              np = (import ./modules/cap-sandbox.nix { inherit lib; }).netProps;
              render = allow: np {
                sandbox = { network = true; egressDeny = [ "198.51.100.0/24" ]; egressAllow = allow; };
              };
              allowsOf = props: lib.filter (lib.hasPrefix "IPAddressAllow=") props;
              hardcoded = allowsOf (render [ ]);
              # POSITIVE CONTROL, same role as specificOk above and for the same reason: an arm that
              # asserted "no Allow entries ever" would be satisfied by a netProps that had stopped
              # rendering operator exceptions entirely -- silently deleting the feature while going
              # green. A declared exception must still render, exactly once and unchanged.
              declaredRenders = allowsOf (render [ "203.0.113.4/32" ]) == [ "IPAddressAllow=203.0.113.4/32" ];
            in lib.assertMsg (hardcoded == [ ] && declaredRenders)
              ("cap-sandbox: hardcodedBlanket control failed. netProps rendered "
               + "[${lib.concatStringsSep " | " hardcoded}] for a capability whose egressAllow is "
               + "EMPTY -- every one of those is hard-coded in modules/cap-sandbox.nix, and any "
               + "Allow entry makes the egressDeny list dead for the range it covers (rule 1 of "
               + "systemd.resource-control(5)); this is the PR #263 defect itself, not the "
               + "declaration path checkAllow guards. Declared-exception control rendered "
               + "correctly: ${lib.boolToString declaredRenders} (must be true -- a netProps that "
               + "dropped operator exceptions altogether would pass the first half).");
          nixpkgs.legacyPackages.${system}.runCommand "cap-sandbox-check" { } ''
            test -s ${nixpkgs.legacyPackages.${system}.writeText "policy.json" sb.policyJson}
            touch $out
          '';

        # Phase 2 · Step 7 (go-live) — the WIRED invoke-seam END-TO-END regression guard. Drives the
        # REAL broker through the REAL store-pinned cap-invoke DISPATCHER + the patchShebangs'd
        # capabilities.list impl (the EXACT artifacts modules/broker.nix now pins into production via
        # cap-invoke-pkg.nix) and asserts a real `tools/call capabilities.list` returns blessed,
        # DATA-fenced content AND leaves the session taint CLEAN — i.e. ORIGIN_BY_CAP[capabilities.
        # list] = TRUSTED holds END-TO-END (go-live gate #1). Neither broker-core (scripted fake seam,
        # UNTRUSTED caps only) nor capabilities (dispatcher driven directly, no broker) exercises this
        # path — this is the ONLY check that proves the wired seam does not spuriously taint a list.
        # A regression fails `nix flake check` (verify under `--option sandbox true`).
        #
        # WP-S2 SCOPE NARROWING, stated so nobody reads more into a green here than is there: this
        # drives `capInvoke.unconfinedWrapper`, which differs from the production wrapper by exactly
        # the two systemd-confinement exports. A check derivation has no D-Bus and no PID 1, so the
        # confined wrapper would (correctly) DENY every call and this check would go red for a
        # reason unrelated to its question. So seam-live proves the WIRING; it proves nothing about
        # the confinement. That evidence is tests/cap-sandbox-battery.sh, on real systemd.
        # WP-S2 / GATE #5(a) — the WRAPPER PIN check. The confinement only ever reaches production
        # through two `export` lines in `mkWrapper`'s `lib.optionalString confined` block
        # (modules/cap-invoke-pkg.nix), and until this check NOTHING asserted they were there.
        #
        # WHY THAT WAS A HOLE, and why it is the same class as the ProtectSystem and runtimePaths
        # findings — a boundary cancelled by something that reads like it belongs, here by an
        # ABSENCE: cap-invoke treats an UNSET `AGENT_OS_CAP_SANDBOX` as "unconfined by config" and
        # direct-execs the impl. That affordance is deliberate and stays (it is the dev/battery path,
        # which has no D-Bus or PID 1). But it means deleting those two export lines does not error
        # anywhere — it silently ships an UNCONFINED seam. And every existing gate stays green:
        #
        #   checks.cap-sandbox        validates the policy's CONTENT, not that anything consumes it
        #   checks.seam-live          drives unconfinedWrapper ON PURPOSE — it cannot notice
        #   cap-sandbox-battery.sh    passes both variables explicitly, so it never reads the wrapper
        #   test-cap-sandbox-confinement  runs that same battery, same explicit env
        #
        # Note the asymmetry this closes: a missing AGENT_OS_SYSTEMD_RUN is a runtime DENY ("NEVER
        # fall back to an unconfined exec"), while a missing policy is a silent unconfined exec. Both
        # arrive from the same two lines. Rather than remove the documented UNSET affordance, assert
        # at BUILD time that production actually pins them.
        #
        # Asserted in BOTH directions on purpose. Requiring the pins in `wrapper` catches deletion;
        # requiring their ABSENCE from `unconfinedWrapper` catches the opposite drift, where someone
        # "fixes" seam-live by confining the test wrapper and the documented delta between the two
        # builds — exactly two exports and nothing else — quietly stops being true.
        cap-wrapper-pinned =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            capInvoke = import ./modules/cap-invoke-pkg.nix { inherit pkgs; };
          in pkgs.runCommand "cap-wrapper-pinned-check" { } ''
            prod=${capInvoke.wrapper}/bin/cap-invoke
            test=${capInvoke.unconfinedWrapper}/bin/cap-invoke

            for v in AGENT_OS_CAP_SANDBOX AGENT_OS_SYSTEMD_RUN; do
              grep -q "export $v=/nix/store/" "$prod" || {
                echo "cap-wrapper-pinned: the PRODUCTION cap-invoke wrapper does not pin $v to a" \
                     "store path. cap-invoke reads an unset AGENT_OS_CAP_SANDBOX as" \
                     "'unconfined by config' and direct-execs the impl, so this ships a seam with" \
                     "NO fs confinement and no error anywhere. See modules/cap-invoke-pkg.nix." >&2
                exit 1
              }
              grep -q "$v" "$test" && {
                echo "cap-wrapper-pinned: the UNCONFINED wrapper sets $v. That wrapper exists for" \
                     "checks.seam-live only, and its whole justification is that it differs from" \
                     "production by exactly these two exports. If it confines, seam-live goes red" \
                     "in a nix sandbox (no D-Bus, no PID 1) and the documented delta is a lie." >&2
                exit 1
              }
            done

            # The pinned policy must be the one DERIVED from the registry, not any old JSON: same
            # store path checks.cap-sandbox and the battery are built from.
            grep -q "export AGENT_OS_CAP_SANDBOX=${
              pkgs.writeText "agent-os-cap-sandbox.json"
                (import ./modules/cap-sandbox.nix { lib = nixpkgs.lib; }).policyJson
            }$" "$prod" || {
              echo "cap-wrapper-pinned: the production wrapper pins AGENT_OS_CAP_SANDBOX to" \
                   "something other than the registry-derived policy." >&2
              exit 1
            }
            touch $out
          '';

        seam-live =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
            capInvoke = import ./modules/cap-invoke-pkg.nix { inherit pkgs; };
          in pkgs.runCommand "seam-live-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/seam-live-battery.sh} ${./bin/broker} ${./bin/taint} ${./bin/audit} \
              ${capInvoke.unconfinedWrapper}/bin/cap-invoke ${capInvoke.capBinDir}/bin ${registryJson} "$work"
            touch $out
          '';

        # PR-A follow-up — the CI-toplevel-build gap ("the real fix"). `nix flake check`
        # EVALUATES the nixosConfigurations but never BUILDS their `system.build.toplevel`, so
        # the nftables `checkRuleset` (`nft --check`) — the ONLY thing that catches a bad egress
        # rule — never ran in CI. That is exactly how the clean-room DHCP rule shipped
        # `agentos-sealed` RED from #21 to #27 (nft 1.0.9 rejects `udp sport X dport Y`, a single
        # `udp` shared across a sport+dport pair, with "No symbol type information"; see
        # modules/clean-room.nix). These two checks close the gap CHEAPLY: each forces the nftables
        # module's OWN `rulesScript` derivation to build (it is one element of the nftables service
        # ExecStart), and that drv's checkPhase runs the EXACT lkl-hijacked `nft --check` the box
        # runs at `nixos-rebuild` (nixpkgs nftables.nix checkPhase, LD_PRELOAD liblkl-hijack). We
        # reuse the module's own check verbatim — no reconstruction, no drift. Referencing the
        # ExecStart store paths in the build script pulls in ONLY that drv (nft + lkl + our
        # ruleset), NOT the whole system toplevel — so `nix flake check` stays fast, matching the
        # design that deliberately holds the seal-faildown VM out of `checks` (packages.${system}
        # comment above). We gate BOTH machines: -sealed (the shipped seal) AND -unsealed (whose
        # provisioning accepts differ, so its ruleset is a distinct nft parse). A MALFORMED egress
        # rule now fails HERE, in CI — not silently at `nixos-rebuild` on the Dell.
        #
        # WHAT THESE DO NOT DO (corrected 2026-08-14, Fable ruling — the previous wording claimed
        # "a re-simplification of any egress rule now fails HERE," and that is FALSE in the
        # direction that hurts). This is a WELL-FORMEDNESS gate. `nft --check` proves a ruleset
        # parses; it cannot know which packets you meant to drop. A WEAKENED rule parses perfectly:
        # `oifname "wg-mesh" accept` — the uid-blind accept of 2fb94c6, which handed the untrusted
        # agent an all-port path to every mesh peer — is valid nftables and sails through both of
        # these green. Malformed and over-permissive are different failure modes and only the first
        # one is caught here. SEMANTICS live in the behavioural VM tests (packages.test-egress-*),
        # which are enforced by .github/workflows/vm-tests.yml — the slow lane. If you are reading
        # this to decide whether an egress diff is covered: these checks are necessary, the slow
        # lane is the one that would notice the wall getting wider.
        # GUARD-OF-THE-GUARD (Fable APPROVE_WITH_CAVEAT, #28): both checks force the module's
        # rulesScript drv to build, but that drv only actually runs `nft --check` when the module's
        # `checkRuleset` is true (nixpkgs nftables.nix — checkPhase is `lib.optionalString
        # cfg.checkRuleset`). If anyone ever set `networking.nftables.checkRuleset = false` the
        # checkPhase would go EMPTY and both of these would go green-while-unvalidated — the single
        # way this whole PR can be silently defeated. Default is true and there's no override in-repo,
        # but for a guard whose entire job is "the wall can't ship red," a comment is weaker than it
        # deserves — so each check eval-time ASSERTS its config's checkRuleset before building.
        nft-ruleset-sealed =
          assert nixpkgs.lib.assertMsg
            self.nixosConfigurations.agentos-sealed.config.networking.nftables.checkRuleset
            "nft-ruleset-sealed would false-PASS: networking.nftables.checkRuleset is false";
          nixpkgs.legacyPackages.${system}.runCommand "nft-ruleset-sealed-check" { } ''
            echo "agentos-sealed nftables ruleset passed nft --check via: ${
              nixpkgs.lib.concatStringsSep " "
                self.nixosConfigurations.agentos-sealed.config.systemd.services.nftables.serviceConfig.ExecStart
            }"
            touch $out
          '';

        # WP-S5 parse gate. A distinct nft parse from nft-ruleset-sealed: the S5 variant renders
        # `meta skuid 350` where the sealed one renders `meta skuid 0`, and a numeric-uid rule is
        # exactly the kind of thing that can be malformed in a way the sealed ruleset never
        # exercises. Same standing caveat as its siblings — see the WHAT THESE DO NOT DO block
        # above: this proves the ruleset PARSES, never that a packet dies, and it is completely
        # blind to the hostname allowlist, which is not in the ruleset at all.
        nft-ruleset-sealed-s5 =
          assert nixpkgs.lib.assertMsg
            self.nixosConfigurations.agentos-sealed-s5.config.networking.nftables.checkRuleset
            "nft-ruleset-sealed-s5 would false-PASS: networking.nftables.checkRuleset is false";
          nixpkgs.legacyPackages.${system}.runCommand "nft-ruleset-sealed-s5-check" { } ''
            echo "agentos-sealed-s5 nftables ruleset passed nft --check via: ${
              nixpkgs.lib.concatStringsSep " "
                self.nixosConfigurations.agentos-sealed-s5.config.systemd.services.nftables.serviceConfig.ExecStart
            }"
            touch $out
          '';

        # WP-S5: the allowlist is only enforced if filtering was COMPILED IN. tinyproxy wraps its
        # entire filter block in `#ifdef FILTER_ENABLE`; built with `--disable-filter` it still
        # ACCEPTS `Filter` and `FilterDefaultDeny` in its config file, logs nothing unusual, and
        # proxies every host you asked it to refuse. Today nixpkgs passes no configureFlags and
        # upstream defaults the flag to yes — an upstream default that this module's whole
        # security property rests on, and that no other gate here would notice changing.
        #
        # The probe is the log string emitted on a refusal ("Proxying refused on filtered"), which
        # lives in the same #ifdef as the filter itself and therefore cannot be present in a
        # binary that cannot filter. Asserted on the exact derivation the module uses, not on a
        # separately-resolved `pkgs.tinyproxy`.
        fetch-proxy-filter-compiled =
          let
            p = nixpkgs.legacyPackages.${system};
            tp = self.nixosConfigurations.agentos-sealed-s5.config.services.tinyproxy.package;
          in
          p.runCommand "fetch-proxy-filter-compiled-check" { } ''
            if ${p.gnugrep}/bin/grep -q "Proxying refused on filtered" ${tp}/bin/tinyproxy; then
              echo "ok: ${tp} has FILTER_ENABLE compiled in"
            else
              echo "FAIL: ${tp}/bin/tinyproxy was built WITHOUT FILTER_ENABLE." >&2
              echo "agentos.fetchProxy.allowedHosts would be accepted and IGNORED." >&2
              exit 1
            fi
            touch $out
          '';

        nft-ruleset-unsealed =
          assert nixpkgs.lib.assertMsg
            self.nixosConfigurations.agentos.config.networking.nftables.checkRuleset
            "nft-ruleset-unsealed would false-PASS: networking.nftables.checkRuleset is false";
          nixpkgs.legacyPackages.${system}.runCommand "nft-ruleset-unsealed-check" { } ''
            echo "agentos (unsealed) nftables ruleset passed nft --check via: ${
              nixpkgs.lib.concatStringsSep " "
                self.nixosConfigurations.agentos.config.systemd.services.nftables.serviceConfig.ExecStart
            }"
            touch $out
          '';

        # Phase 2 (OPEN lane) — the DROPPED-IMPORT guard ("imports can't ship green").
        # SCAR (#42 -> fixed #44): a rebase resolved configuration-open.nix's `imports = [ … ]`
        # conflict by DELETING the whole block. Every Phase-2 module is self-contained, so with
        # nothing importing them the agentos-open toplevel still EVALUATED and BUILT clean — it just
        # silently shipped an image with no calendar, no desktop, no settings, and no in-image brain.
        # `nix flake check` never builds the open toplevel (the SAME gap the nft checks above close
        # for the sealed egress rule), so it read as merged+green.
        #
        # WHY A MARKER-ASSERT, NOT A TOPLEVEL BUILD (Rabbot's choice-of-form to me): building
        # `agentos-open.config.system.build.toplevel` is the strongest guard but REALIZES the ~4.68GB
        # qwen2.5 model FOD (modules/model-open.nix) on every CI run — bandwidth-hostile for a public
        # repo. This asserts, at EVAL time (zero realization, DVo-cheap), that each of the fourteen
        # bundled modules left its unique fingerprint in the built config. Drop any import and its
        # marker goes unset -> this throws, naming the module. Same dropped-import class, caught for
        # free. (Upgrade to a full toplevel build later if a fetch-capable CI ever wants belt+braces.)
        #
        # GUARD-OF-THE-GUARD: each marker must stay UNIQUE to exactly one module (a fingerprint no
        # other module sets), or a dropped import could be masked by an unrelated one. Today:
        #   calendar-open -> `agos-cal` CLI in systemPackages   (the agent's read/write calendar hand)
        #   desktop-open  -> programs.hyprland.enable           (the reproducible compositor)
        #   settings-open -> `agos-sys` CLI in systemPackages   (the agent's settings hand)
        #   model-open    -> systemd service `agos-seed-model`  (the in-image brain, Dillon 8988)
        #   genesis-open  -> `agent-brain` in systemPackages    (the genesis-locked soul-reading brain)
        #   calculator-open -> `agos-calc` CLI in systemPackages (the agent's calculator hand)
        #   files-open    -> `agos-files` CLI in systemPackages (the agent's read-only files hand)
        #   email-open    -> `thunderbird` in systemPackages     (human mail GUI; agent hand = MCP, escalated)
        #   mail-secret-open -> systemd service `agos-mail-token-preflight` (the (A) MCP-email token scaffold,
        #                     agent-side of email; NOT a new dozen app — Rabbot RULING-A)
        #   notes-open    -> `agos-notes` CLI in systemPackages (the agent's notes read/write hand)
        #   docs-open     -> `agos-doc` CLI in systemPackages  (the agent's read-only PDF-content hand)
        #   media-open    -> `agos-media` CLI in systemPackages (the agent's read-only image/AV probe hand)
        #   web-open      -> `agos-web` CLI in systemPackages  (the agent's read-only web-content hand;
        #                     human half = firefox. Browser AUTOMATION is a SEPARATE later increment.)
        #   mail-proton-bridge-open -> systemd service `agos-mail-proton-preflight` (Proton-via-Bridge
        #                     cred scaffold, the "both" parallel to Gmail; NOT a new dozen app)
        agentos-open-imports =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            lib  = nixpkgs.lib;
            cfg  = self.nixosConfigurations.agentos-open.config;
            hasPkg = n: lib.any (p: lib.hasInfix n (p.name or "")) cfg.environment.systemPackages;
          in
            assert lib.assertMsg (hasPkg "agos-cal")
              "agentos-open-imports: calendar-open.nix is NOT imported — agos-cal missing from systemPackages (dropped imports block? see #42/#44).";
            assert lib.assertMsg (hasPkg "gnome-calendar" && cfg.services.radicale.enable)
              "agentos-open-imports: calendar-open.nix GUI half is missing — gnome-calendar absent from systemPackages or services.radicale disabled (the loopback CalDAV that shares the agent vdir with the GUI window; see calendar-GUI-bump go 9047).";
            assert lib.assertMsg cfg.programs.hyprland.enable
              "agentos-open-imports: desktop-open.nix is NOT imported — programs.hyprland.enable is false.";
            assert lib.assertMsg (hasPkg "agos-sys")
              "agentos-open-imports: settings-open.nix is NOT imported — agos-sys missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-seed-model" cfg.systemd.services)
              "agentos-open-imports: model-open.nix is NOT imported — the in-image brain (agos-seed-model, Dillon 8988) is absent.";
            assert lib.assertMsg (builtins.hasAttr "agos-seed-model-3b" cfg.systemd.services)
              "agentos-open-imports: model-3b-open.nix is NOT imported — the additive NON-DEFAULT 2nd brain (agos-seed-model-3b, qwen2.5:3b-augur) is absent.";
            assert lib.assertMsg (hasPkg "agent-brain")
              "agentos-open-imports: genesis-open.nix is NOT imported — agent-brain (the genesis-locked soul-reading brain) missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-selfimprove")
              "agentos-open-imports: selfimprove-open.nix is NOT imported — agos-selfimprove missing from systemPackages. The whole orchestration engine (observe/propose/cycle/surface/lcm/advisor/subagents) then exists ONLY as CI fixtures, green and absent from every machine, which is exactly the state this module was written to end.";
            assert lib.assertMsg (builtins.hasAttr "agos-selfimprove" cfg.systemd.timers)
              "agentos-open-imports: selfimprove-open.nix installs the engine but NOTHING RUNS IT — the agos-selfimprove timer is absent. A package nothing invokes is the same defect one step later.";
            assert lib.assertMsg (hasPkg "agos-key-drift")
              "agentos-open-imports: key-drift-open.nix is NOT imported — agos-key-drift missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-key-drift" cfg.systemd.timers)
              "agentos-open-imports: key-drift-open.nix installs the scanner but NOTHING RUNS IT — the agos-key-drift timer is absent. An undeclared root key is found by a scan that FIRES, and a package sitting unrun on disk is the same shape as the hand-written authorized_keys it was written to catch.";
        assert lib.assertMsg (hasPkg "agos-user-drift")
          "agentos-open-imports: user-drift-open.nix is NOT imported — agos-user-drift missing from systemPackages.";
        assert lib.assertMsg (builtins.hasAttr "agos-user-drift" cfg.systemd.timers)
          "agentos-open-imports: the user/group scanner is installed but NOTHING RUNS IT — the agos-user-drift timer is absent. mutableUsers=true means a console-added wheel member persists across every rebuild; a scanner sitting unrun on disk is the same shape as the hand-edited /etc/group it exists to catch.";
            assert lib.assertMsg (hasPkg "agos-tailscale-ssh-reassert")
              "agentos-open-imports: tailscale-ssh-reassert-open.nix is NOT imported — agos-tailscale-ssh-reassert missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-tailscale-ssh-reassert" cfg.systemd.timers)
              "agentos-open-imports: the Tailscale SSH re-assert is installed but NOTHING RUNS IT — the agos-tailscale-ssh-reassert timer is absent. This unit exists precisely BECAUSE tailscaled-autoconnect asserts --ssh once at first auth and never again; shipping it without a timer reproduces the one-shot defect it was written to close, and the door it holds open is the mesh's own write path onto this box.";
            assert lib.assertMsg (hasPkg "agos-calc")
              "agentos-open-imports: calculator-open.nix is NOT imported — agos-calc missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-files")
              "agentos-open-imports: files-open.nix is NOT imported — agos-files missing from systemPackages.";
            assert lib.assertMsg (hasPkg "thunderbird")
              "agentos-open-imports: email-open.nix is NOT imported — thunderbird (the human mail GUI) missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agos-mail-token-preflight" cfg.systemd.services)
              "agentos-open-imports: mail-secret-open.nix is NOT imported — the (A) MCP-email token scaffold (agos-mail-token-preflight) is absent.";
            assert lib.assertMsg (hasPkg "agos-notes")
              "agentos-open-imports: notes-open.nix is NOT imported — agos-notes missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-doc")
              "agentos-open-imports: docs-open.nix is NOT imported — agos-doc missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-media")
              "agentos-open-imports: media-open.nix is NOT imported — agos-media missing from systemPackages.";
            assert lib.assertMsg (hasPkg "agos-web")
              "agentos-open-imports: web-open.nix is NOT imported — agos-web missing from systemPackages.";
            # GUARD-OF-THE-GUARD (Hermes, 08-09): the import asserts are present, but a module's
            # acceptance BATTERY could be deleted and the build would stay green — same silent-degrade
            # class as a dropped import. Assert each ambient hand's battery file exists in tests/.
            # (Flake source is in the store, so builtins.pathExists resolves repo-relative.)
            assert lib.assertMsg (builtins.pathExists ./tests/calendar-battery.py)
              "agentos-open-imports: calendar-open battery missing (tests/calendar-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-calc-battery.py)
              "agentos-open-imports: calculator-open battery missing (tests/agos-calc-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-sys-battery.py)
              "agentos-open-imports: settings-open battery missing (tests/agos-sys-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-files-battery.py)
              "agentos-open-imports: files-open battery missing (tests/agos-files-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-notes-battery.py)
              "agentos-open-imports: notes-open battery missing (tests/agos-notes-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-doc-battery.py)
              "agentos-open-imports: docs-open battery missing (tests/agos-doc-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-media-battery.py)
              "agentos-open-imports: media-open battery missing (tests/agos-media-battery.py deleted?).";
            assert lib.assertMsg (builtins.pathExists ./tests/agos-web-battery.py)
              "agentos-open-imports: web-open battery missing (tests/agos-web-battery.py deleted?).";
            assert lib.assertMsg (builtins.hasAttr "agos-mail-proton-preflight" cfg.systemd.services)
              "agentos-open-imports: mail-proton-bridge-open.nix is NOT imported — the Proton-via-Bridge cred scaffold (agos-mail-proton-preflight) is absent.";
            pkgs.runCommand "agentos-open-imports-check" { } ''
              echo "agentos-open imports all fourteen Phase-2 modules: calendar(agos-cal+gnome-calendar/radicale) desktop(hyprland) settings(agos-sys) model(agos-seed-model) genesis(agent-brain) calculator(agos-calc) files(agos-files) email(thunderbird) mail-secret(agos-mail-token-preflight) notes(agos-notes) docs(agos-doc) media(agos-media) web(agos-web) mail-proton(agos-mail-proton-preflight) + additive NON-DEFAULT 3B(agos-seed-model-3b, qwen2.5:3b-augur)"
              touch $out
            '';


        # SEALED lane — the same DROPPED-IMPORT guard, for the SHIPPED machine.
        #
        # WHY THIS EXISTS (measured, not assumed — Mirror 2026-08-20): `agentos-open-imports` above
        # closes this hole for the OPEN variant only. The sealed variant's module list is `baseModules`,
        # inline in this file, and nothing asserted over it. I control-armed the gap rather than
        # arguing it: commented `./modules/mcp.nix` out of baseModules and ran the ship gate. Result —
        # `all checks passed!`, rc=0. The sealed machine would have shipped with no MCP surface and
        # `nix flake check` would have called it green.
        #
        # WHY THE VM TESTS DO NOT COVER IT: they `inherit baseModules`, so they DO see a drop — but
        # they are deliberately OUT of `checks` (they boot VMs) and run in the separate vm-tests.yml
        # matrix. The ship gate is `nix flake check`. A guarantee that lives only in the other lane is
        # not a guarantee at the gate, and only some of the fifteen are observed by a VM test at all.
        # Same seam lesson as the engine: green batteries per component say nothing about whether the
        # component is IN the machine.
        #
        # GUARD-OF-THE-GUARD (inherited from the open guard, and it bites harder here): each marker
        # must be UNIQUE to exactly one module. Package markers below are matched by EXACT name, not
        # infix — `audit`/`taint`/`mcp`/`confirm` are short enough that `hasInfix` would happily match
        # an unrelated package and mask the very drop this is meant to catch.
        #
        # Two of the fifteen are options-only in the sealed base by design (mesh-wireguard-sealed and
        # fetch-proxy ship INERT, enabled per-variant), so their fingerprint is the OPTION EXISTING,
        # not a service running. Asserting `.enable` there would assert the variant, not the import.
        agentos-sealed-imports =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            lib  = nixpkgs.lib;
            cfg  = self.nixosConfigurations.agentos-sealed.config;
            hasPkg = n: lib.any (p: (p.pname or p.name or "") == n) cfg.environment.systemPackages;
          in
            assert lib.assertMsg (hasPkg "agent-shell")
              "agentos-sealed-imports: agent-shell.nix is NOT imported — `agent-shell` missing from systemPackages. That module is the part that makes this Agent OS rather than a NixOS box.";
            assert lib.assertMsg (builtins.hasAttr "agent-os-pull-model" cfg.systemd.timers)
              "agentos-sealed-imports: brain.nix is NOT imported — the agent-os-pull-model timer is absent, so the sealed image has no path to a model.";
            assert lib.assertMsg (builtins.hasAttr "cleanRoom" cfg.agentos)
              "agentos-sealed-imports: clean-room.nix is NOT imported — options.agentos.cleanRoom is absent. The egress wall would be missing from the SEALED image, which is the one property the sealed variant exists to have.";
            assert lib.assertMsg (builtins.hasAttr "agent-os-identity-boot" cfg.systemd.services)
              "agentos-sealed-imports: identity.nix is NOT imported — the agent-os-identity-boot oneshot is absent, so NO participant is ever minted and $AGENT_OS_AUDIT_SIGNER can only fail closed. That is the exact state this module was written to end (task 324 step 1): the layer worked, nothing called it.";
            assert lib.assertMsg (hasPkg "audit")
              "agentos-sealed-imports: audit.nix is NOT imported — `audit` missing from systemPackages.";
            assert lib.assertMsg (hasPkg "taint")
              "agentos-sealed-imports: taint.nix is NOT imported — `taint` missing from systemPackages.";
            assert lib.assertMsg (hasPkg "mcp")
              "agentos-sealed-imports: mcp.nix is NOT imported — `mcp` missing from systemPackages. This is the exact drop that was measured to ship green before this guard existed.";
            assert lib.assertMsg (hasPkg "broker")
              "agentos-sealed-imports: broker.nix is NOT imported — `broker` missing from systemPackages. Capability invocation would have no front door.";
            assert lib.assertMsg (hasPkg "confirm")
              "agentos-sealed-imports: confirm.nix is NOT imported (or its sandbox precondition went false) — the confirm wrapper is missing from systemPackages.";
            assert lib.assertMsg (builtins.hasAttr "agentos-seal-check" cfg.systemd.services)
              "agentos-sealed-imports: seal-check.nix is NOT imported — the agentos-seal-check service is absent, so a broken seal would fail SILENT instead of loud (the whole point of that module).";
            assert lib.assertMsg (cfg.users.users.root.hashedPasswordFile == "/etc/agent-os/break-glass.hash")
              "agentos-sealed-imports: break-glass.nix is NOT imported — root's hashedPasswordFile is not the break-glass hash, so the ONE interactive root door (tty3) is gone.";
            assert lib.assertMsg (builtins.hasAttr "meshWireguard" cfg.agentos)
              "agentos-sealed-imports: mesh-wireguard-sealed.nix is NOT imported — options.agentos.meshWireguard is absent (WP-S1 sealed-lane mesh; options-only here by design).";
            assert lib.assertMsg (builtins.hasAttr "fetchProxy" cfg.agentos)
              "agentos-sealed-imports: fetch-proxy.nix is NOT imported — options.agentos.fetchProxy is absent (WP-S5; options-only and INERT here by design).";
            assert lib.assertMsg (builtins.hasAttr "agent-os-system-set" cfg.systemd.services)
              "agentos-sealed-imports: system-set.nix is NOT imported — the agent-os-system-set unit is absent.";
            assert lib.assertMsg cfg.boot.plymouth.enable
              "agentos-sealed-imports: boot-branding.nix is NOT imported — boot.plymouth.enable is false.";
            pkgs.runCommand "agentos-sealed-imports-check" { } ''
              echo "agentos-sealed imports all fifteen baseModules: agent-shell brain clean-room(options) identity audit taint mcp broker confirm seal-check break-glass mesh-wireguard-sealed(options) fetch-proxy(options) system-set boot-branding"
              touch $out
            '';


        # R1 runtime (tier 0, item 3) — THE BOOT PREWARM MUST WARM THE MODEL THE SESSION USES.
        #
        # `environment.variables` builds the LOGIN env (/etc/profile). systemd units do NOT
        # inherit it. So `agos-boot-prewarm` ran agent-brain with OLLAMA_MODEL unset and fell
        # through to that script's own `qwen3.5:9b` literal — while the session runs
        # `qwen3.5:9b-agentos` (base + LoRA, a DIFFERENT ollama tag). Two consequences, and the
        # second is the expensive one:
        #   1. The prewarm's KV slot belonged to a model the session never asks for, so the
        #      first real message still paid the full cold prefill. The unit's entire purpose
        #      was defeated, and it reported success either way — the "3 minutes is too long"
        #      complaint the unit exists to answer was never actually addressed on this variant.
        #   2. keep_alive=-1 is set on BOTH paths, so the box ends up holding TWO ~6.5GB 9B
        #      models resident. On the Dell (21GB usable, memory-bandwidth-bound) that is the
        #      dual-resident contention Scout ranked as the single biggest latency cause.
        #
        # The fix derives the unit's env FROM the session attrset rather than re-spelling the
        # tag, so the two cannot drift again. This check is the regression guard for that: it
        # asserts the shipped unit carries an OLLAMA_MODEL and that it EQUALS the session's.
        #
        # Because the value is derived, "they are equal" is near-tautological against today's
        # source — which is exactly why the check carries two PRE-FIX ARMS. `noEnv` replays the
        # unit as it actually shipped (no environment at all) and `drifted` replays the obvious
        # future regression (someone hardcodes a literal that later diverges from the session).
        # Both must be REJECTED by the same predicate. Without them this check would stay green
        # against the very shape it was written to catch, which is the failure mode this repo
        # keeps re-earning: an arm that cannot fail is not evidence.
        prewarm-model-matches-session =
          let
            lib  = nixpkgs.lib;
            cfg  = self.nixosConfigurations.agentos-open.config;
            # The predicate, written ONCE and applied to the real config and to both pre-fix
            # arms — so the arms exercise the same rule production is judged by, not a
            # paraphrase of it (two halves spelling one rule in two languages is how these
            # guards rot).
            ok = unitEnv: sessionVars:
              (unitEnv ? OLLAMA_MODEL)
              && (sessionVars ? OLLAMA_MODEL)
              && (unitEnv.OLLAMA_MODEL == sessionVars.OLLAMA_MODEL);
            sessionVars = cfg.environment.variables;
            unitEnv     = cfg.systemd.services.agos-boot-prewarm.environment;
            noEnv       = { };
            drifted     = { OLLAMA_MODEL = "qwen3.5:9b"; };
          in
            assert lib.assertMsg (ok unitEnv sessionVars)
              ("prewarm-model-matches-session: agos-boot-prewarm does not warm the session's model. "
               + "unit OLLAMA_MODEL=" + (toString (unitEnv.OLLAMA_MODEL or "<unset>"))
               + " session OLLAMA_MODEL=" + (toString (sessionVars.OLLAMA_MODEL or "<unset>"))
               + ". An unset unit env is NOT a harmless default: agent-brain falls back to its own "
               + "qwen3.5:9b literal, the prewarm warms a tag the session never asks for, and "
               + "keep_alive=-1 then holds two 9B models resident.");
            assert lib.assertMsg (! (ok noEnv sessionVars))
              "prewarm-model-matches-session: PRE-FIX ARM 1 (unit with no environment) was ACCEPTED — the predicate cannot detect the shape it was written for.";
            assert lib.assertMsg (! (ok drifted { OLLAMA_MODEL = "qwen3.5:9b-agentos"; }))
              "prewarm-model-matches-session: PRE-FIX ARM 2 (hardcoded, drifted literal) was ACCEPTED — the predicate compares nothing.";
            nixpkgs.legacyPackages.${system}.runCommand "prewarm-model-matches-session" { } ''
              echo "agos-boot-prewarm warms ${unitEnv.OLLAMA_MODEL}, the same tag the session runs; two pre-fix arms rejected"
              touch $out
            '';

        # The engine MANIFEST, read off the BUILT ARTIFACT — not off the comment that claims it.
        #
        # selfimprove-open.nix's header used to say it installs "every `agos_*` module". That was
        # false by two: there are ten `agos_*.py` and the module ships eight, deliberately (the
        # comms pair is the stack-side brain-comms shadow migration, which nothing in the engine
        # imports). Nobody would have noticed from CI, because the claim lived in a comment and
        # comments are not gated. I corrected the comment in the same commit as this check — but a
        # corrected comment can drift again tomorrow, which is exactly the status-word defect this
        # whole engine was written to end. So the scope claim is now a MECHANISM.
        #
        # It reads the shipped `agos-selfimprove` wrapper, extracts the engine store path out of its
        # PYTHONPATH line, and lists what is actually in there. That is the artifact a running
        # machine would import — not the source tree, and not the file's prose about itself. Add a
        # ninth module or drop one of the eight and this goes red with a diff.
        #
        # It deliberately asserts BOTH directions. Presence alone would stay green if the comms pair
        # were quietly added later, and absence alone would stay green if the engine shipped empty.
        agos-engine-manifest =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            lib  = nixpkgs.lib;
            cfg  = self.nixosConfigurations.agentos-open.config;
            sip  = lib.head (lib.filter (p: (p.pname or p.name or "") == "agos-selfimprove")
                     cfg.environment.systemPackages);
          in
            pkgs.runCommand "agos-engine-manifest-check" { buildInputs = [ sip ]; } ''
              set -euo pipefail
              engine=$(sed -n 's|.*PYTHONPATH="\(/nix/store/[^"]*\)/lib/agos.*|\1|p' \
                         ${sip}/bin/agos-selfimprove | head -1)
              if [ -z "$engine" ]; then
                echo "agos-engine-manifest: BLIND — could not extract the engine store path from the"
                echo "agos-selfimprove wrapper. That is not a pass. The wrapper's PYTHONPATH line"
                echo "changed shape, so this check no longer reads the artifact it claims to read."
                exit 1
              fi
              got=$(cd "$engine/lib/agos" && ls *.py | sort | tr '\n' ' ')
              want="agos_advisor.py agos_cycle.py agos_events.py agos_lcm.py agos_observe.py agos_propose.py agos_subagents.py agos_surface.py "
              if [ "$got" != "$want" ]; then
                echo "agos-engine-manifest: the shipped engine is NOT the eight modules it claims."
                echo "  want: $want"
                echo "  got:  $got"
                echo "If you ADDED a module on purpose, update \$want here and the SCOPE paragraph in"
                echo "modules/selfimprove-open.nix — both, or the next reader inherits the same"
                echo "mismatch between what ships and what the file says ships."
                exit 1
              fi
              for f in agos_comms_shadow.py agos_comms_live.py; do
                if [ -e "$engine/lib/agos/$f" ]; then
                  echo "agos-engine-manifest: $f is shipping in the product image. It is the stack-side"
                  echo "brain-comms shadow migration (a live-mesh observer), nothing in the engine"
                  echo "imports it, and it has no business in a machine image. Remove it, or if this is"
                  echo "intentional, say so in selfimprove-open.nix's SCOPE paragraph first."
                  exit 1
                fi
              done
              echo "agos-engine-manifest: the shipped engine is exactly the eight ($want) and the comms pair is absent"
              touch $out
            '';

        # Orchestration engine · Phase 1 — the `agos-events` append-only event-log library's CONTRACT
        # BATTERY. Proves, against the MULTI-WRITER-across-SyncThing reality Geist ruled load-bearing
        # (per-(topic,machine) single-writer files; merge-read ordered by ts→machine→id; per-(consumer,
        # topic,machine) cursors): multi-writer exactly-once (no double/miss), replay-from-0, defer()
        # redelivery with NO done + head-of-line by writer (Geist PIN #1), deterministic ordering
        # (ts dominates, machine breaks ties), first-class done/await_done threaded by corr_id, and
        # routing on (topic, to) — NEVER by parsing payloads (Geist ruling #2). Zero external deps;
        # a regression fails `nix flake check`. (Phase-1 library; the shadow brain-comms migration
        # rides on top and is measured separately — Rabbot owns that before/after readout.)
        agos-events-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-events-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./tests/agos-events-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Orchestration engine · HARNESS-MAP slice 3 — SUBAGENT FAN-OUT WITH TYPED YIELDS
        # (agos_subagents, atop agos_events). The point of the module is that a worker's yield is not
        # a result until it TYPE-CHECKS, so the battery proves the validator BOTH ways: malformed
        # yields (missing field, wrong type, bad list element, undeclared extra, not-a-dict) become
        # loud `error` events with specific reasons — never silent skips, never half-trusted results —
        # AND a known-good yield is ACCEPTED (the control arm, without which a reject-everything
        # validator would pass the negative half and be worthless). Also proves: the concurrency cap
        # is real (observed max in flight, plus a check that the run actually overlapped, so the cap
        # assertion cannot pass trivially on a serial run); a raising worker is isolated from its
        # siblings; Geist PIN #1 — a deferred unit emits no result, no error, and the run emits NO
        # `done`, control-armed against a clean run that does; the run budget returns in wall-clock
        # (it caught a real defect — the pool's context manager joined the abandoned thread); gather()
        # replays a run from the log alone, isolated by corr_id; bool does not pass as int; and a bad
        # schema raises before any work runs. Zero external deps; a regression fails `nix flake check`.
        agos-subagents-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-subagents-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_subagents.py} "$work/agos_subagents.py"
              cp ${./tests/agos-subagents-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Orchestration engine · HARNESS-MAP slice 4 — THE ADVISOR / WATCHER (agos_advisor, atop
        # agos_events). HARNESS-MAP records the merge-queue stall as a watcher GAP: work went quiet and
        # nothing was watching for quiet. Every other failure we have announces itself; silence does
        # not, and a queue with nothing moving looks exactly like a queue with nothing to do. The
        # governing risk is that **a watcher that never fires is indistinguishable from a healthy
        # system** — the instrument error with the stakes inverted, since the false all-clear is the
        # answer everyone hoped for. So NO rule is tested in one direction only: the scar rule fires as
        # a blocker on a quiet open request AND stays silent on a completed run, a young request, and
        # corr_id-less chatter; error-rate fires at threshold AND is quiet under it. Also proves:
        # progress is not a stall (quiet measured from the LAST event, so a slow run is left alone);
        # idempotency across a FRESH advisor, deduped from the log rather than memory, because a spammy
        # watcher gets muted and a muted watcher is the original gap; a raising rule becomes a loud
        # `rule-failure` blocker instead of a silent skip; `examined` separates "nothing is wrong" from
        # "I looked at nothing", which are otherwise the same empty finding list; rules get a read-only
        # view with no emit/log/cursor reachable; and advice lands on a SEPARATE `<topic>-advice` topic
        # so the watcher never observes its own output. Zero external deps; a regression fails check.
        agos-advisor-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-advisor-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_advisor.py} "$work/agos_advisor.py"
              cp ${./tests/agos-advisor-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Orchestration engine · HARNESS-MAP slice 5 — the LCM COMPACTION LAYER's CONTRACT BATTERY
        # (agos_lcm, atop agos_events). Sells exactly one hard guarantee: compaction is LOSSLESS —
        # a compacted record must reconstruct EXACTLY what it replaced, or it is deletion wearing
        # compaction's name. Enforced structurally (the digest stores verbatim events; the summary is
        # derived and disposable, so a bad summariser cannot make the store lossy) and by test: the
        # battery CORRUPTS a store on purpose and asserts the round-trip check FIRES, because a
        # losslessness test that cannot detect a lossy store would pass against a module that stored
        # nothing at all. Also pinned: the event log is READ-ONLY here (byte hashes of every .jsonl
        # asserted unchanged across a compaction — a log something else prunes is not append-only);
        # corr_id-less events are grouped, never dropped; duplicate sids are refused rather than
        # silently overwriting history; a raising summariser loses the index line, never the data.
        # Zero external deps; a regression fails `nix flake check`.
        agos-lcm-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-lcm-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_lcm.py} "$work/agos_lcm.py"
              cp ${./tests/agos-lcm-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Self-improvement loop · phase OBSERVE (+COMPARE) — CONTRACT BATTERY (agos_observe).
        # Instantiation B of HARNESS-SELFIMPROVE, DELIBERATELY PARTIAL: no PROPOSE, no APPLY,
        # because APPLY means the loop edits its own harness and the auto-merge question is open
        # with Dillon. Case G asserts that limit STRUCTURALLY (no apply/propose entry point, no
        # subprocess/shutil import) so the read-only half cannot quietly grow the ability to act.
        # The load-bearing rule: RE-OBSERVATION MUST NOT MANUFACTURE RECURRENCE — a cadence
        # observer re-reads overlapping history, and a naive counter would promote a ONE-time
        # failure to a LESSON by seeing it twice, i.e. fabricate the evidence for its own
        # proposals. Case A proves re-observing is a no-op; case A2 is its CONTROL ARM (a store
        # that recorded NOTHING would pass case A identically) and proves a genuinely distinct
        # second occurrence does count and does promote. Also asserted: the event log and
        # turn-log are byte-unchanged (read-only); the multi-writer turn-log is filtered on
        # `event` per Augur's frozen schema; unparseable lines are COUNTED, never swallowed.
        # Zero external deps; a regression fails `nix flake check`.
        agos-observe-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-observe-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_observe.py} "$work/agos_observe.py"
              cp ${./tests/agos-observe-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Self-improvement loop · phase PROPOSE — CONTRACT BATTERY (agos_propose).
        # Phase 3 of 4 for instantiation B, and a SEPARATE module from agos_observe on purpose:
        # OBSERVE's header forbids growing an act-path inside the read half, and this file honours
        # the same rule one level down. APPLY is NOT here — Dillon's open Q1 (does APPLY ever
        # auto-merge?) governs APPLY, not this; a PROPOSE that emits a record and stops is inside
        # EVERY possible answer to Q1, which is why it is shippable now.
        # The load-bearing rule: A PROPOSAL IS A DOCUMENT, NEVER AN ACTION — case A asserts that
        # STRUCTURALLY (no subprocess/shutil/urllib/socket import, no apply/merge/commit/push entry
        # point, no file opened for writing). Case B is the blast-radius floor, with the gate
        # definition ITSELF on the deny list and traversal normalised so it cannot be walked around.
        # Case E drives a FORBIDDEN target through the REAL propose() path and watches it go red —
        # Augur's insistence that a green security leg needs the run where it fails — and E2 is its
        # control arm (an allowed target on the same path must emit, or "refused" is just what the
        # module does to everything). Case C proves REJECTED is TERMINAL and keyed on CONTENT hash,
        # so trivial rewording cannot resurrect a rejected proposal by attrition. Case D control-arms
        # the drafter itself. Zero external deps; a regression fails `nix flake check`.
        agos-propose-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-propose-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_propose.py} "$work/agos_propose.py"
              cp ${./tests/agos-propose-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Self-improvement loop · the SURFACING half of STORE (agos_surface). HARNESS-SELFIMPROVE
        # instantiation B specifies the store as "markdown + SQLite mirror, SURFACED AS A BRAIN-COMM";
        # the SQLite half shipped with OBSERVE, this is the surfacing half. It is the ONLY module in
        # the loop that writes a file, so the battery's job is proving that write cannot become APPLY:
        # writing `docs/lessons.md` (a proposal's TARGET) ENACTS the proposal, while writing a digest
        # that SAYS a proposal is pending reports on it. emit() refuses any destination matching a
        # carried proposal's target — including ./, //, .. and case variants, which is exactly how the
        # check gets bypassed — and any deny-listed path, reusing PROPOSE's list rather than copying it.
        # A2/B2 control-arm both guards (same basename allowed when nothing targets it; benign paths
        # still written). Case C carries the third state to the layer a HUMAN reads: a blind cycle must
        # not render as a quiet one, and an ABSENT cycle report is its own state rather than assumed
        # healthy. E runs it against the real stores and asserts the proposal's target was never created
        # on disk. Contains no APPLY. Zero external deps.
        agos-surface-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-surface-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agos_observe.py} "$work/modules/agos_observe.py"
              cp ${./modules/agos_propose.py} "$work/modules/agos_propose.py"
              cp ${./modules/agos_surface.py} "$work/modules/agos_surface.py"
              cp ${./tests/agos-surface-contract.py} "$work/tests/agos-surface-contract.py"
              cd "$work"
              python3 tests/agos-surface-contract.py
              touch $out
            '';

        # Self-improvement loop · the CADENCE RUNNER (agos_cycle) — the seam OBSERVE→COMPARE→PROPOSE.
        # The three phases each had a green battery and NO CALLER; three libraries are not a loop, and
        # the seam between them had never executed. So this battery drives the REAL agos_observe into
        # the REAL agos_propose — nothing at the seam is faked, since a composition test that fakes the
        # composition just re-proves the halves. Load-bearing rule: AN EMPTY CYCLE MUST SAY WHY IT WAS
        # EMPTY — healthy / source MISSING / source UNREADABLE are three facts, and a self-improvement
        # loop that reports perfect health while blind never stops looking fine. Case C pins the exact
        # instance (the read half returns ([],0) for a missing file, identical to a clean log) and C2/E2
        # are its control arms. D asserts the COMPOSITION is idempotent across cadence re-runs, which is
        # a different claim from each half being idempotent. Contains no APPLY. Zero external deps.
        agos-cycle-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-cycle-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agos_observe.py} "$work/modules/agos_observe.py"
              cp ${./modules/agos_propose.py} "$work/modules/agos_propose.py"
              cp ${./modules/agos_surface.py} "$work/modules/agos_surface.py"
              cp ${./modules/agos_cycle.py}   "$work/modules/agos_cycle.py"
              cp ${./modules/agos_events.py}  "$work/modules/agos_events.py"
              cp ${./modules/agos_advisor.py} "$work/modules/agos_advisor.py"
              cp ${./tests/agos-cycle-contract.py} "$work/tests/agos-cycle-contract.py"
              cd "$work"
              python3 tests/agos-cycle-contract.py
              touch $out
            '';

        # Orchestration engine · Phase 1 part 2 — the SHADOW brain-comms migration's CONTRACT BATTERY
        # (agos_comms_shadow, atop agos_events). Observes + parallel-emits, changes NO routing (the live
        # `_done/`+dispatch-watchers stay truth). Proves the migration is faithful BEFORE any cutover:
        # PORT PARITY — two INDEPENDENT route derivations agree on every comm (route_from_to(parse) ==
        # file_route(globs)); ROUTE TRUTH over every documented hazard (single/broadcast/the broadcast_gap
        # where to-all misses scout/phoenix/geist/rebound/combined air-<brain>/to-mesh orphan→nobody);
        # EMIT idempotency; DONE corr_id threading; EXACTLY-ONCE cursor'd delivery with the mtime-refire
        # double-fire structurally killed + orphan→no-brain; author-host machine partition in arrival
        # (mtime) order. A regression fails `nix flake check`.
        agos-comms-shadow-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-comms-shadow-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_comms_shadow.py} "$work/agos_comms_shadow.py"
              cp ${./tests/agos-comms-shadow-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # Orchestration engine · Phase 1 CUTOVER — the LIVE routing layer's CONTRACT BATTERY
        # (agos_comms_live, atop agos_events + agos_comms_shadow). Where the shadow battery proves the
        # route DERIVATIONS are faithful, this proves the LIVE MECHANICS the cutover turns on: the
        # react-on-delta consumer (wake_pending fires exactly the NEW summonses routed to a brain, and a
        # re-run yields NOTHING — the mtime-refire double-fire structurally killed by the cursor), route
        # fidelity under the live path (broadcast gap, rebound, un-addressed brain), fresh-delta ordering,
        # emit idempotency, and Saga's read-only cohesion sweep (a request with no done reads UNRESOLVED,
        # clears when its done lands, and advances no cursor). A regression fails `nix flake check`.
        agos-comms-live-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agos-comms-live-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              cp ${./modules/agos_events.py} "$work/agos_events.py"
              cp ${./modules/agos_comms_shadow.py} "$work/agos_comms_shadow.py"
              cp ${./modules/agos_comms_live.py} "$work/agos_comms_live.py"
              cp ${./tests/agos-comms-live-contract.py} "$work/contract.py"
              cd "$work"
              python3 contract.py
              touch $out
            '';

        # WP-A2 (task 287) — the pluggable brain-provider config's CONTRACT BATTERY, plus its
        # integration wiring into agent-brain.py. Two batteries share one check because
        # wiring-battery.py is the same K6 slice (PR #77) driving the real consumer, and both
        # need the identical pyyaml-carrying python. providers-battery.py proves
        # modules/providers.py standalone: floor/escalate resolve, required fields (kind,
        # cost_tier) enforced, api_key_ref must be a secret reference (a literal key is
        # rejected — the fleet-bleed scar, 2026-08-06), the degraded flag, fail-loud on a
        # present-but-invalid yaml. wiring-battery.py proves agent-brain.py actually WIRES to
        # it (env-default fallback with no config, floor model resolution, floor-without-
        # model-key fallback) and — a HARD REQUIREMENT, not a skip — that pyyaml is importable
        # in this python: a missing pyyaml previously let agent-brain silently degrade every
        # boot to legacy OLLAMA_MODEL with an unseen stderr warning (K6 post-merge bug, PR
        # #77); a battery that SKIPs on missing pyyaml would hide that exact regression. Before
        # this check, both batteries ran in NEITHER gate. A regression fails `nix flake check`.
        providers-contract =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            pyWithYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
          in pkgs.runCommand "providers-contract-check" { nativeBuildInputs = [ pyWithYaml ]; } ''
            work="$(mktemp -d)"
            mkdir -p "$work/modules" "$work/tests"
            cp ${./modules/providers.py} "$work/modules/providers.py"
            cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
            cp ${./tests/providers-battery.py} "$work/tests/providers-battery.py"
            cp ${./tests/wiring-battery.py} "$work/tests/wiring-battery.py"
            cp ${./tests/cost-cap-battery.py} "$work/tests/cost-cap-battery.py"
            cp ${./tests/summon-consent-battery.py} "$work/tests/summon-consent-battery.py"
            cd "$work"
            PYTHONPATH=modules python3 tests/providers-battery.py
            python3 tests/wiring-battery.py
            # cost-cap breaker (HARNESS-MAP guardrail 3): limits validation, yaml>env>default
            # precedence, token-trip refusal + transcript stubs, loud hop exhaustion.
            python3 tests/cost-cap-battery.py
            # summon_claude consent gate (Rabbot door (i), 2026-09-02): the tool spawns the
            # operator's Claude CLI, and until this gate the only thing between a model-emitted
            # call and that subprocess was prompt text. Arms drive `ok_to_summon` — the same
            # function the deployed path calls — plus `_summon_claude` itself with the CLI
            # stubbed, so there is no fixture-only route (#256's lesson).
            python3 tests/summon-consent-battery.py
            touch $out
          '';

        # 2026-09-02 — the providers.yaml the DELL ACTUALLY BOOTS WITH, parsed by the real
        # loader. Until this check existed, nothing in this tree ever read that file: every
        # battery above authors its own fixture, so `modules/escalate-secret-open.nix` could
        # ship a typo'd api_key_ref, a key path inside the world-readable /nix/store, or a
        # floor provider with no model, and `nix flake check` would stay green all the way to
        # a box whose brain cannot read its own config. Same shape as 0620404 — "CI was green
        # on a package that cannot arm a budget".
        #
        # The bytes come from the EVALUATED configuration's environment.etc entry, NOT from a
        # copy of the module kept in step by hand; a check that reads a duplicate is only ever
        # testing the duplicate. The battery also carries seven NEGATIVE arms (N1-N6) that feed
        # deliberately broken configs through the same predicates and require rejection, plus
        # N0 as the permitting arm for that harness — without N0, a predicate that failed
        # EVERYTHING would make every negative arm pass for the wrong reason.
        shipped-providers =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            pyWithYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
            shipped = self.nixosConfigurations.agentos-open.config.environment.etc."agent-os/providers.yaml".source;
          in pkgs.runCommand "shipped-providers-check" { nativeBuildInputs = [ pyWithYaml ]; } ''
            work="$(mktemp -d)"
            mkdir -p "$work/modules" "$work/tests"
            cp ${./modules/providers.py} "$work/modules/providers.py"
            cp ${./tests/shipped-providers-battery.py} "$work/tests/shipped-providers-battery.py"
            cd "$work"
            PYTHONPATH=modules SHIPPED_PROVIDERS=${shipped} python3 tests/shipped-providers-battery.py
            touch $out
          '';

        # 2026-09-02 — the SEALED variants must carry NO cloud provider config. Today this holds
        # by construction: modules/escalate-secret-open.nix lives only in `openModules`, so
        # `agentos-sealed` never gets an /etc/agent-os/providers.yaml. But "true by construction"
        # is true until someone moves one line, and nothing asserted it. A sealed image that
        # shipped a cloud `api_key_ref` would have only the nftables wall behind it — a
        # config-level breach of the seal that every existing egress check would still pass.
        #
        # THE CONTROL ARM IS INSIDE THE CHECK, and it has to be: the passing condition here is an
        # ABSENT attribute, which is exactly the state where an assertion goes vacuous without
        # anyone noticing. So the third value below re-evaluates the sealed config with the
        # escalate module deliberately grafted on via extendModules, and the check FAILS unless
        # the predicate SEES it there. Absence only means something once presence has been shown
        # to be detectable.
        sealed-no-cloud-provider =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            hasProviders = c: c.config.environment.etc ? "agent-os/providers.yaml";
            sealed   = hasProviders self.nixosConfigurations.agentos-sealed;
            sealedS5 = hasProviders self.nixosConfigurations.agentos-sealed-s5;
            grafted  = hasProviders (self.nixosConfigurations.agentos-sealed.extendModules {
              modules = [ ./modules/escalate-secret-open.nix ];
            });
            b = x: if x then "true" else "false";
          in pkgs.runCommand "sealed-no-cloud-provider-check" { } ''
            fail=0
            echo "  agentos-sealed    ships agent-os/providers.yaml: ${b sealed}   (want false)"
            echo "  agentos-sealed-s5 ships agent-os/providers.yaml: ${b sealedS5} (want false)"
            echo "  CONTROL: sealed + escalate module grafted on:     ${b grafted}  (want true)"
            [ "${b sealed}"   = "false" ] || { echo "  FAIL sealed carries a cloud provider config"; fail=1; }
            [ "${b sealedS5}" = "false" ] || { echo "  FAIL sealed-s5 carries a cloud provider config"; fail=1; }
            [ "${b grafted}"  = "true"  ] || { echo "  FAIL control arm: the predicate cannot SEE a providers.yaml even when one is grafted on — the two arms above are vacuous"; fail=1; }
            [ "$fail" = "0" ] || exit 1
            echo "  ALL PASS"
            touch $out
          '';

        # 2026-09-02 — a LINT for test arms that swallow their own failure signal.
        #
        # providers-battery arm J was written as: call the function inside a `try`, `raise
        # AssertionError` if it did NOT reject, and treat `except Exception` as the pass.
        # AssertionError is an Exception, so the handler caught the arm's own failure signal
        # and reported it as the rejection under test. The arm printed PASS *precisely when*
        # the module silently degraded — a complete inversion, in the arm whose whole job was
        # to catch that degrade. Reproduced by stubbing load_providers to return {}.
        #
        # A battery cannot catch this: the failure mode IS a green run, so there is no run to
        # observe. Hence a structural scan, with its own control + permitting arms inline (a
        # detector that finds nothing reports a clean tree, which is indistinguishable from
        # health). Verified against the pre-fix tree: the lint names providers-battery.py:277.
        tests-no-self-swallowing-arms =
          let pkgs = nixpkgs.legacyPackages.${system};
          in pkgs.runCommand "tests-no-self-swallowing-arms" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"; mkdir -p "$work/tests"
            cp -r ${./tests}/*.py "$work/tests/"
            # The .sh copy is not cosmetic. The lint grew a shell half on 2026-09-02, and this
            # line is the SET it is handed: with only *.py copied the shell arms scan zero files
            # and the check is green while covering nothing -- the same "sound check, wrong set"
            # defect the lint exists to catch, one level up, in its own sandbox. The verdict now
            # prints both counts so a zero here is visible in the build log rather than implied.
            cp -r ${./tests}/*.sh "$work/tests/"
            cp ${./tests/no-self-swallowing-arms.py} "$work/lint.py"
            python3 "$work/lint.py" "$work/tests"
            touch $out
          '';

        # 2026-09-02 — the escalate key PREFLIGHT has six branches and until now exactly ONE of
        # them had ever executed. The box's steady state is "no key placed", so every green
        # `Result=success` I have ever read off `agos-escalate-key-preflight` came from the
        # absent branch returning 0. The mode / owner / empty / trailing-newline rejections —
        # the entire reason the unit exists — were unexecuted code guarding a secret.
        #
        # It was untestable BY CONSTRUCTION: the key path was baked in, so no fixture could
        # reach it. So the script now takes two env overrides, used only here, and arms S1/S2
        # against the risk that introduces: S1 asserts the SHIPPED unit sets neither, and S2 is
        # the control arm for S1 — it grafts a unit that DOES set one and fails unless the
        # detector sees it. An absence assertion is vacuous until presence is shown detectable.
        #
        # C1 inside the battery is the other control arm: every functional arm passes the owner
        # override, so without C1 the override could simply be disabling the owner check and all
        # five would still be green. C1 drops the override and requires the check to fire.
        #
        # The binary under test is the unit's own ExecStart, read out of the evaluated config —
        # not a rebuild of the script here. Same rule as shipped-providers: test the artifact
        # that ships, never a copy kept in step by hand.
        escalate-preflight =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            open = self.nixosConfigurations.agentos-open;
            unitOf = c: c.config.systemd.services.agos-escalate-key-preflight;
            execStart = (unitOf open).serviceConfig.ExecStart;
            hasOverride = c:
              let e = (unitOf c).environment or { };
              in (e ? AGOS_ESCALATE_KEY) || (e ? AGOS_ESCALATE_KEY_OWNER);
            shippedOverrides = hasOverride open;
            detectorArmed = hasOverride (open.extendModules {
              modules = [{
                systemd.services.agos-escalate-key-preflight.environment.AGOS_ESCALATE_KEY =
                  "/tmp/grafted-override";
              }];
            });
            b = x: if x then "1" else "0";
          in pkgs.runCommand "escalate-preflight-check" { } ''
            PREFLIGHT=${execStart} \
            SHIPPED_OVERRIDES=${b shippedOverrides} \
            DETECTOR_ARMED=${b detectorArmed} \
            bash ${./tests/escalate-preflight-battery.sh}
            touch $out
          '';

        # WP-A2 (task 287) — bin/mem's CONTRACT BATTERY: the memory-as-filesystem layer every
        # other Agent OS layer is built on (remember / recall / tree / cap). `mem-cap` above
        # covers only the A2 mem.* capability IMPLS (cap-mem-remember / cap-mem-recall); this
        # covers the tool itself — UTC-Z timestamped writes (never local-time drift), append-
        # never-overwrite on a repeated key, key slugification confined to MEM_ROOT (no ../
        # traversal), the seeded home-tree structure on first use, recall's fuzzy ranked search
        # with terminal-escape neutralization, and the cap add/list round-trip. Zero external
        # deps (stdlib only, same as bin/mem itself); invoked via `sys.executable bin/mem`, so
        # no chmod/shebang patching is needed here. Before this check this battery ran in
        # NEITHER gate. A regression fails `nix flake check`.
        # Item 4 (Hyprland Lua migration) — agent-brain.py's DESKTOP HANDS, red-armed.
        # Hyprland 0.56's Lua config changes what `hyprctl dispatch` MEANS: under a hyprland.lua
        # it EVALUATES its argument as Lua (`hl.dispatch(<arg>)`) instead of parsing a hyprlang
        # command word. `open_url` built its dispatch string from a MODEL-SUPPLIED url, so the
        # mechanical translation would have put attacker-influenced text inside an expression the
        # compositor evaluates — arbitrary Lua in the process that owns the display. The ruling
        # (Rabbot, 2026-08-30) was to REMOVE the sink rather than escape it, on the grounds that an
        # escaper is wrong once and wrong forever. This battery therefore does not test an escaper:
        # it asserts the sink is UNREACHABLE — a Lua-escape payload in the url produces ZERO hyprctl
        # invocations and reaches firefox as one literal argv element — and that arrange_windows
        # dispatches only fixed table values keyed by its closed enum, with an unknown key
        # dispatching nothing. That last one is the CONTROL ARM: without it, a do_tool that
        # dispatched nothing at all would satisfy the no-injection half vacuously. Shown firing, not
        # merely green — run against the pre-fix open_url it fails four arms (verified 2026-08-30).
        # pyyaml is in scope for the same reason providers-contract needs it: agent-brain.py imports
        # providers.py behind an `except Exception`, so a python without it imports a silently
        # degraded module. A regression fails `nix flake check`.
        brain-dispatch-contract =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            pyWithYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
          in pkgs.runCommand "brain-dispatch-contract-check"
            { nativeBuildInputs = [ pyWithYaml ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
              cp ${./modules/providers.py} "$work/modules/providers.py"
              cp ${./tests/brain-dispatch-battery.py} "$work/tests/brain-dispatch-battery.py"
              cd "$work"
              python3 tests/brain-dispatch-battery.py
              touch $out
            '';

        # The IN-LOOP stderr->journal copy (#285 follow-up, Geist RULED 2026-09-05T18:17Z).
        #
        # #285 could only tee the PRE-loop writes: the turn loop runs inside patch_stdout(raw=True),
        # which swaps sys.stderr for a proxy onto stdout, so the router-leg latency lines never
        # touch fd 2 and the unit's tee has nothing to copy. That gap was recorded as row A2 of
        # #285's own firing table. The copy lives at ONE site, the guard, so these arms test the
        # guard-side object rather than the eleven write call sites.
        #
        # A2 is the permitting twin -- without it a copy that installed unconditionally would pass
        # every other arm while double-writing the pre-loop path into #285's tee. A8 is the pre-fix
        # arm: the same writes with no copy installed must produce ZERO journal lines, or the gap
        # this check exists for is unproven and the greens above mean nothing. A9 is the call-site
        # arm -- a correct tee that nothing enters is exactly the "lives only in prose" failure one
        # level up, and it also pins the ExitStack ORDER, which decides whether sys.stderr is
        # handed back to a proxy that is still alive.
        brain-stderr-journal-contract =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            pyWithYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
          in pkgs.runCommand "brain-stderr-journal-contract-check"
            { nativeBuildInputs = [ pyWithYaml ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
              cp ${./modules/providers.py} "$work/modules/providers.py"
              cp ${./tests/brain-stderr-journal-battery.py} "$work/tests/brain-stderr-journal-battery.py"
              cd "$work"
              python3 tests/brain-stderr-journal-battery.py
              touch $out
            '';

        # The R1 CONTEXT BOUND, checked against the shipped module (tier-0 item 3).
        #
        # Two halves, both of which are easy to ship inert. `num_ctx` is a JSON key: omit it
        # and the daemon quietly uses its own default, and nothing anywhere reports that the
        # window you declared is not the window you got. The history bound is worse — an
        # unbounded `msgs` list looks IDENTICAL to a bounded one until the session is long
        # enough to matter, at which point the front of the conversation slides out of the
        # window and the brain simply stops knowing what it was told. Neither failure has a
        # symptom a user can name.
        #
        # The arm that earns its keep is F: it runs the NAIVE trimmer ("drop the oldest k
        # messages") over the same fixture and asserts it leaves the transcript cut MID-GROUP
        # — an assistant's tool_calls separated from the tool results answering it, which the
        # Anthropic transport rejects outright. Without that arm, the boundary rule in
        # trim_history() could be passing on an input no trimmer could get wrong.
        # E is the control arm: an under-budget history must come back UNCHANGED, so a
        # trimmer that deleted everything cannot satisfy the other arms vacuously.
        # The key-drift SCANNER discriminates. Note what this check is and is not: it
        # proves the instrument can tell a planted undeclared key from a declared one, it
        # does NOT say anything about whether any particular box is clean. That second
        # question is answered by the systemd unit on the running machine and by nothing
        # here, because an evaluation cannot see a file a person typed at a console.
        #
        # Arm A's fixture is the actual key removed from the Dell on 2026-08-31, and it is
        # the pre-fix arm: without it, a scanner that had been quietly reduced to `exit 0`
        # would ship looking exactly like this one. Arm B is the control that stops a
        # scanner returning 1 unconditionally from passing A — and it is deliberately
        # textually different from the declared line, so it also proves the comparison is
        # by fingerprint rather than by string. Arm E is the one this lane keeps
        # relearning: an instrument that cannot read its input must not report clean.
        key-drift-contract =
          let p = nixpkgs.legacyPackages.${system};
          in p.runCommand "key-drift-contract-check"
            { nativeBuildInputs = with p; [ bash coreutils gnugrep gnused gawk openssh ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules/agos-key-drift" "$work/tests"
              cp ${./modules/agos-key-drift/agos-key-drift.sh} "$work/modules/agos-key-drift/agos-key-drift.sh"
              cp ${./tests/key-drift-battery.sh} "$work/tests/key-drift-battery.sh"
              cd "$work"
              bash tests/key-drift-battery.sh modules/agos-key-drift/agos-key-drift.sh
              touch $out
            '';

        # Nothing this repo DOWNLOADS AND EXECUTES is executed unverified — and the helper
        # that enforces that is shown REFUSING before its acceptance is trusted.
        #
        # WHY THIS EXISTS. `bin/setup-brain.sh` ran `curl -fsSL https://claude.ai/install.sh
        # | bash` on a box being provisioned. Whatever the socket produced was executed, and
        # the pipe makes the failure mode PARTIAL EXECUTION — bash runs the first half of a
        # script whose second half never arrived. There was no point in that pipeline at
        # which bytes could have been rejected, so there was nothing for a check to check.
        #
        # THE ARMS RUN OFFLINE ON PURPOSE. The fixtures are `file://` URLs fetched through
        # the real curl path, so the seam under test is the shipped one, not a mock — and a
        # sandbox with no network cannot turn this check into a test of Anthropic's uptime.
        # The live pin was exercised separately against the real URL before it was recorded
        # (verified 0 on the true digest, 1 on a corrupted one, no file left behind).
        #
        # ARM I IS THE ONE THAT KEEPS THIS HONEST. It asserts the CALL SITE stayed converted.
        # A verified-fetch helper sitting unused beside a live `curl | bash` is the same
        # shape as a scanner installed with no timer — and arm I was RED against the
        # pre-fix `bin/setup-brain.sh` before the conversion landed.
        #
        # NOT COVERED, stated so a green is not over-read: the README bootstrap
        # (`curl .../install.sh | sudo bash`) is trust-on-first-use by construction — the
        # thing that would verify it is the thing being fetched. Pinning what the installer
        # then pulls narrows the window; closing it needs an out-of-band digest or a
        # signature and is NOT claimed by this check.
        supply-chain-pinning-contract =
          let p = nixpkgs.legacyPackages.${system};
          in p.runCommand "supply-chain-pinning-contract-check"
            { nativeBuildInputs = with p; [ bash coreutils gnugrep gnused gawk curl ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/bin" "$work/tests" "$work/supply-chain"
              cp ${./bin/fetch-verified.sh}            "$work/bin/fetch-verified.sh"
              cp ${./bin/setup-brain.sh}               "$work/bin/setup-brain.sh"
              cp ${./tests/fetch-verified-battery.sh}  "$work/tests/fetch-verified-battery.sh"
              cp ${./supply-chain/pins.txt}            "$work/supply-chain/pins.txt"
              cd "$work"
              bash tests/fetch-verified-battery.sh bin/fetch-verified.sh "$work"

              # Every pin line must carry a full 64-hex digest and an https URL. A truncated
              # or placeholder digest would still "match" nothing and read as a live pin.
              if ! awk '$1 !~ /^#/ && NF { if ($2 !~ /^[0-9a-f]{64}$/ || $3 !~ /^https:/) { print "bad pin line: " $0; bad=1 } } END { exit bad }' supply-chain/pins.txt; then
                echo "supply-chain: malformed pin in pins.txt"; exit 1
              fi
              touch $out
            '';

        # Declared state is the whole of WHO CAN LOG IN and WHO IS PRIVILEGED — the half
        # agos-key-drift does not cover.
        #
        # WHY THIS EXISTS. The open variant sets `users.mutableUsers = true` on purpose (dev
        # box; runtime passwd changes are meant to persist). That is declared, not drift — but
        # it means /etc/passwd and /etc/group are hand-editable and survive every rebuild, so a
        # console-added user or a name appended to `wheel` is invisible to this flake, to the
        # module, and to the key scanner. Same class as the undeclared root key removed
        # 2026-08-31, one file over.
        #
        # ARM D IS WHY THE SCANNER IS WORTH SHIPPING. The obvious check reads /etc/group's
        # member lists — and a box where someone set a user's PRIMARY GID to wheel's gid reads
        # perfectly clean under it, because that user never appears in a member list at all.
        # Arm D performs exactly that edit and asserts the scan fails. Arm I is its control: a
        # user whose primary group IS its declared group must stay silent, so a scanner that
        # flagged every gid match cannot pass D.
        #
        # THE FIXTURES ARE THE DELL, read off the box 2026-08-31 — agent (uid 1000, primary
        # group `users`), operator (the sole declared member of `wheel`), root, sshd nologin.
        # Arm B is that box clean and runs FIRST, so a scanner that flags everything cannot
        # pass the drift arms. Every other arm is that box with one hand edit.
        #
        # NOT COVERED, stated so a green is not over-read: the tailscale-ssh user mapping is a
        # SEPARATE surface (Tailscale SSH authenticates against tailnet ACLs and never consults
        # /etc/passwd for authorization) and is deliberately not folded in here. And a flake
        # check cannot see a file someone typed at a console — the battery proves the scanner,
        # the systemd timer on the box is what actually looks.
        user-drift-contract =
          let p = nixpkgs.legacyPackages.${system};
          in p.runCommand "user-drift-contract-check"
            { nativeBuildInputs = with p; [ bash coreutils gnused gnugrep python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules/agos-user-drift" "$work/tests"
              cp ${./modules/agos-user-drift/agos-user-drift.py} "$work/modules/agos-user-drift/agos-user-drift.py"
              cp ${./tests/user-drift-battery.sh} "$work/tests/user-drift-battery.sh"
              cd "$work"
              bash tests/user-drift-battery.sh modules/agos-user-drift/agos-user-drift.py
              touch $out
            '';

        brain-context-contract =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            pyWithYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
          in pkgs.runCommand "brain-context-contract-check"
            { nativeBuildInputs = [ pyWithYaml ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
              cp ${./modules/providers.py} "$work/modules/providers.py"
              cp ${./tests/brain-context-battery.py} "$work/tests/brain-context-battery.py"
              cd "$work"
              python3 tests/brain-context-battery.py
              touch $out
            '';

        # The brain's HAND, resolved at build time rather than by name. A battery with no
        # runner is four batteries and no runner — the arms exist here or they are prose.
        shell-resolve-contract =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            pyWithYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);
          in pkgs.runCommand "shell-resolve-contract-check"
            { nativeBuildInputs = [ pyWithYaml ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
              cp ${./modules/providers.py} "$work/modules/providers.py"
              cp ${./modules/genesis-open.nix} "$work/modules/genesis-open.nix"
              cp ${./tests/shell-resolve-battery.py} "$work/tests/shell-resolve-battery.py"
              cd "$work"
              python3 tests/shell-resolve-battery.py
              touch $out
            '';

        # A tool call that renders as prose is a CLAIM — the model asked to act, nothing
        # acted, and the next turn narrated a result it never produced. Observed live on
        # qwen3.5:9b (Dillon's Dell TUI photo, 2026-08-31): an XML/Hermes `<tool_call>` block
        # that extract_tools()'s JSON-shaped fallback did not match. This gate holds the XML
        # branch AND the two controls that keep it honest — structured `tool_calls` still win,
        # and the older JSON fallback is not displaced — plus a PRE-FIX arm that runs the old
        # JSON-only regex on the live input and requires it to find NOTHING. Without that arm
        # the battery could be passing on an input the old code already handled.
        xml-toolcall-contract =
          nixpkgs.legacyPackages.${system}.runCommand "xml-toolcall-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
              cp ${./tests/xml-toolcall-battery.py} "$work/tests/xml-toolcall-battery.py"
              cd "$work"
              python3 tests/xml-toolcall-battery.py
              touch $out
            '';

        # The two CUMULATIVE spend ceilings (Rabbot's GO 2026-08-31, built before any
        # credential exists — the bleed scar's ordering). `max_output_tokens_per_turn`
        # already in the brain is a RATE LIMITER: it bounds one turn and forgets, so a
        # respawn loop pays it forever and trips nothing. This gate holds the ceilings that
        # accumulate, and — the half that matters — that a TYPO does not silently disable
        # them. A disabled ceiling and a working one are indistinguishable until the bill
        # arrives, so arm G walks "off"/"none"/"0"/"-1"/"true"/"1e6" and requires each to
        # refuse rather than pass as a cap. Two controls keep the rest honest: J (no ceiling
        # configured → inert, so the other arms are not passing off a stage that refuses
        # unconditionally) and K (a ceiling configured with the module ABSENT still refuses).
        spend-ceiling-contract =
          nixpkgs.legacyPackages.${system}.runCommand "spend-ceiling-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/modules" "$work/tests"
              cp ${./modules/agent-brain.py} "$work/modules/agent-brain.py"
              cp ${./modules/spend_ceiling.py} "$work/modules/spend_ceiling.py"
              cp ${./tests/spend-ceiling-battery.py} "$work/tests/spend-ceiling-battery.py"
              cd "$work"
              python3 tests/spend-ceiling-battery.py
              touch $out
            '';

        mem-contract =
          nixpkgs.legacyPackages.${system}.runCommand "mem-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/bin" "$work/tests"
              cp ${./bin/mem} "$work/bin/mem"
              cp ${./tests/mem-battery.py} "$work/tests/mem-battery.py"
              cd "$work"
              python3 tests/mem-battery.py
              touch $out
            '';

        # WP-A2 (task 287) — agent-loop's TOOL-DISPATCH MECHANICS contract battery, sibling to
        # the `agent-loop` check above (which proves chat/answer/tool-spin mechanics end to end
        # against ollama-stub). This one isolates the dispatch primitives: discover_tools()
        # marshals a well-formed JSON-RPC 2.0 tools/call the REAL bin/mcp accepts; dispatch()
        # unwraps a broker data_result, and surfaces a broker deny / unknown-capability /
        # malformed-verdict as a fail-closed deny (never an exception); the MAX_DENIALS(3) and
        # MAX_TOOL_HOPS(8) caps; and _clean()'s terminal control/escape stripping ahead of the
        # tty. Drives the REAL bin/mcp piped into tests/broker-stub.py (scripted verdicts, zero
        # model needed) — same "real wall, scripted broker" shape as `agent-loop`. The battery
        # locates bin/agent-loop and the wall relative to its own copied location, so the repo-
        # relative bin/ + tests/ layout is reconstructed in the check's scratch dir; the
        # AGENT_OS_MCP / AGENT_OS_BROKER / PYTHONPATH env vars are still passed explicitly per
        # the battery's own documented contract. Before this check this battery ran in NEITHER
        # gate. A regression fails `nix flake check`.
        agent-loop-dispatch-contract =
          nixpkgs.legacyPackages.${system}.runCommand "agent-loop-dispatch-contract-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              mkdir -p "$work/bin" "$work/tests" "$work/modules"
              cp ${./bin/agent-loop} "$work/bin/agent-loop"
              cp ${./bin/mcp} "$work/bin/mcp"
              cp ${./tests/agent-loop-dispatch-battery.py} "$work/tests/agent-loop-dispatch-battery.py"
              cp ${./tests/broker-stub.py} "$work/tests/broker-stub.py"
              cd "$work"
              # AGENT_OS_STRICT=1 turns the battery's "bin/mcp not found -> SKIP, exit 0" into
              # a hard failure. That skip is right for a hand-run from the wrong directory; here
              # it would mean a `cp` above silently stopped happening and this check went green
              # having executed nothing.
              AGENT_OS_MCP="$work/bin/mcp" AGENT_OS_BROKER="$work/tests/broker-stub.py" \
                AGENT_OS_STRICT=1 \
                PYTHONPATH="$work/modules" python3 tests/agent-loop-dispatch-battery.py
              touch $out
            '';
      };
    };
}
