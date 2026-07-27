{
  description = "Agent OS — a computer whose shell is an agent, not a desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";  # Dell Latitude = Intel x86_64
    in {
      # The whole machine. `agent-shell.nix` is the part that makes it Agent OS;
      # `configuration.nix` is boring base plumbing (bootloader, user, network).
      nixosConfigurations.agentos = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./configuration.nix
          ./modules/agent-shell.nix
        ];
      };

      # Prove boot-and-talk in a VM BEFORE it ever touches the Dell:
      #   nix build .#vm && ./result/bin/run-*-vm
      packages.${system}.vm =
        self.nixosConfigurations.agentos.config.system.build.vm;
    };
}
