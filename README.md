# Space Engineers 2 dedicated server on Ubuntu with AMP

Run a headless **Space Engineers 2 4.0.0.3169** server with **Y51 SE2 Online 0.18.4** on Ubuntu 24.04 x86-64. CubeCoders AMP manages the Linux process, console commands, player count, world saves and shutdown. Players use the matching Y51 client plugin. This is an unofficial community port.

The original deployment hosted a world in Docker, accepted an in-game LAN connection, saved on shutdown and loaded an imported world with a planet. The server runs with .NET 10 and Pulsar for Linux. LinuxCompat2 and native wrappers still use components from the Windows game runtime. The installation below reconstructs that deployment from upstream downloads on your own host.

## What you need

- Ubuntu 24.04 x86-64, Docker Engine, CubeCoders AMP with Docker instances enabled, Git and Python 3.12.
- The **Source code 0.18.4 ZIP** and **Standalone server 0.18.4-standalone.1** from yellow51. Sign in with Steam to verify ownership. Keep the source ZIP intact and extract the standalone release into a separate private directory. [Y51 downloads](https://se2-online.yellow51.dev/#downloads)
- Internet access for the initial build and Steam networking. Game files, credentials and saves stay on the host. Allow enough free space for the extracted standalone, a second instance copy, build files and Docker layers.

## Install on the AMP target

Run these commands as the Linux user who owns the two downloads. The example assumes the extracted standalone folder is called `standalone` and contains `runtime/` and `runtime-manifest.json`. The image must be built on the Docker daemon used by the AMP target.

```bash
git clone https://github.com/vileider/se2-ubuntu-amp.git
cd se2-ubuntu-amp
sudo docker build -t local/se2-y51-amp:0.18.4 .
python3 prepare-runtime.py --sudo-docker \
  "$HOME/Downloads/Y51_SE2Online-Source-0.18.4.zip" \
  "$HOME/Downloads/standalone" \
  "$HOME/se2-stage/SpaceEngineers2"
```

Preparation verifies the engine and source archive, patches a temporary copy of Y51, builds the plugins and stages a new instance directory. A successful file check prints `SE2_AMP_FILES_OK`. The first in-game connection is the end-to-end test.

1. In the ADS controller, add `vileider/se2-ubuntu-amp:main` to **Configuration Repositories** and select **Fetch Latest**. Create a Linux Docker instance using **KNT / Space Engineers 2 Online (Y51)**. Keep the game stopped until its files are in place.
2. Copy the prepared directory into the new instance. For a standard installation using the system account `amp`, replace `MySE201` below with the instance's actual directory name. The copy requires an empty destination so it cannot overwrite a running world.

   ```bash
   (
     set -eu
     inst=/home/amp/.ampdata/instances/MySE201
     sudo test -f "$inst/AMPConfig.conf"
     stage="$HOME/se2-stage/SpaceEngineers2"
     sudo test -s "$stage/Game2/SpaceEngineers2.dll"
     incoming=$(sudo mktemp -d "$inst/.se2-install.XXXXXXXX")
     sudo cp -a -- "$stage" "$incoming/SpaceEngineers2"
     sudo chown -R --reference="$inst/AMPConfig.conf" "$incoming/SpaceEngineers2"
     sudo mv -T -- "$incoming/SpaceEngineers2" "$inst/SpaceEngineers2"
     sudo rmdir -- "$incoming"
   )
   ```

3. In **Configuration → Serwer**, set the session name, optional password and player limit. **Widocznosc w katalogu Y51** controls public listing. AMP writes that choice into `state/server.json` whenever the game starts. In **Swiat**, choose a world name and the starting scenario for a new world. `EmptyWorld` starts empty.
4. Assign **UDP 27016** for the game and **UDP 27017** for the Steam query. If the game port changes, use the next port for queries. Click **Start** and wait for `SE2_HOSTING_READY` in the console. This means the current process reports a loaded world and a Steam server identity. In the Y51 client choose **Multiplayer → Direct connect** and enter the Ubuntu host address and game port.

## Saves and operation

Use **Stop** in AMP for a final save. Look for `Potwierdzony zapis koncowy: True` in the console. Back up both `SpaceEngineers2/profile/` and `SpaceEngineers2/state/` while the game is stopped. The first contains the world and the second contains the resume information and private server configuration. For an existing AMP installation using this port, restore those directories together and keep the same world name and scenario in AMP. The public template starts with directory listing disabled.

## Versions and credits

| Component | Version |
| --- | --- |
| Space Engineers 2 engine | 4.0.0.3169, supplied by the host |
| Y51 SE2 Online | 0.18.4, patched during local build |
| Pulsar Linux x64 | v2.4.3 |
| CometWorks LinuxCompat2 | `f713880a05e1ffad2d6b489a6c1bc3c36103e030` |
| CometWorks Linux dependencies / native wrappers | v1.0.42 / v1.0.66 |
| Steamworks SDK Redist | SteamCMD application 1007, Linux x64 |

Y51 SE2 Online is provided courtesy of **yellow51 and Vyrex Interactive LLC**. The original license, including its free-distribution, non-commercial and attribution conditions, accompanies the patch. The game and its runtime belong to Keen Software House. Each host obtains its own copy. [Y51 license](LICENSE-Y51.txt)

Pulsar and LinuxCompat2 are fetched from their respective upstream projects. Their licenses and those of their dependencies apply to those components. [Pulsar](https://github.com/SpaceGT/Pulsar) · [LinuxCompat2](https://github.com/CometWorks/linux-compat2) · [Linux dependencies](https://github.com/CometWorks/linux-dependencies) · [Native wrappers](https://github.com/CometWorks/linux-native-wrappers)

For the AMP launcher and template checks, run `python3 -B -m unittest -v test_service` from this repository. Those tests exercise configuration, readiness reporting and save acknowledgements. A multiplayer smoke test requires the game files and a Steam connection.
