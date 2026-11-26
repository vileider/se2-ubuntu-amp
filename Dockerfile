FROM mcr.microsoft.com/dotnet/runtime:10.0-noble@sha256:64a538092439382d055c87c0f49092efb8bca883c736c54ae523da7fd8e02d18 AS dotnet
FROM steamcmd/steamcmd:ubuntu-24@sha256:73d04ab4dfd57cd87421764d8e7c6fb990ee3d0835f9ee1d4a0f19e76500371d AS steam
RUN steamcmd +force_install_dir /redist +login anonymous +app_update 1007 validate +quit \
    && test -s /redist/linux64/steamclient.so

FROM cubecoders/ampbase:ubuntu-24.04@sha256:51bb270d84f94f064001af034dd5f56ec5f1293d6cc404dd6ea41658bbbf9af9
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 libvulkan1 libicu74 libssl3t64 libgssapi-krb5-2 zlib1g libstdc++6 ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=dotnet /usr/share/dotnet/ /opt/se2-amp/dotnet/
COPY --from=steam /redist/linux64/ /opt/se2-steam/sdk64/
COPY service.py panel_addons.py /opt/se2-amp/
RUN /opt/se2-amp/dotnet/dotnet --list-runtimes | grep '^Microsoft.NETCore.App 10\.' \
    && test -s /opt/se2-steam/sdk64/steamclient.so && python3 -m py_compile /opt/se2-amp/service.py
ENTRYPOINT ["/ampstart.sh"]
CMD []
