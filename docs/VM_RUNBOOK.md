# VM runbook (VMware Fusion, Apple Silicon, Ubuntu Server 22.04 arm64)

Every step is a short command typed in the VM console. The code reaches
the VMs through a Fusion shared folder, not a network: the VMs stay
attached only to vmnet3-6. Helper scripts live in `tools/vm/`. Scratch
files and logs go to `/var/tmp/a3/` on each VM.

Interface map (from the handover; `tools/vm/setup.sh` has it built in):

| VM | script call |
|----|-------------|
| client | `netcfg/client.sh enp2s0 enp26s0` |
| r1 | `netcfg/r1.sh enp2s0 enp26s0` |
| r2 | `netcfg/r2.sh enp2s0 enp26s0 enp3s0` |
| server | `netcfg/server.sh enp2s0` |

The code is pure Python and does not care about the CPU architecture.
Your VMs are arm64; the graders' x86-64 needs nothing different.

## Step 0: get the code onto the VMs (once)

On the Mac:
```
cd ~ && git clone https://github.com/vanshiitd/col334_a3.git
cd col334_a3 && git checkout claude/cool-brown-wn7vdx
mkdir -p results
```
In Fusion, for each of the four VMs: Settings > Sharing > enable Shared
Folders > add `~/col334_a3`, name it `a3`, Read & Write.

In each VM (the mount then comes back automatically after every boot):
```
sudo mkdir -p /mnt/hgfs
echo '.host:/ /mnt/hgfs fuse.vmhgfs-fuse defaults,allow_other,nofail 0 0' | sudo tee -a /etc/fstab
sudo mount /mnt/hgfs
cd /mnt/hgfs/a3 && ls
```
When I push fixes, run `git pull` in `~/col334_a3` on the Mac. All four
VMs see the change at once.

## Step 1: preflight (each VM)

```
bash tools/vm/preflight.sh | tee results/preflight-$(hostname).txt
```
Check, or send me the four `results/preflight-*.txt` files:
- `python3` 3.10.x, `iptables` present.
- `netem` present on r1 and r2, `tbf` present on r2 (see step 2 if not).
- `held:` lists `linux-generic linux-image-generic linux-headers-generic`,
  and the kernel is 5.15.0-119.
- the driver and offload line of each interface. In particular, tell me
  if `large-receive:on` appears on the client or server.

## Step 2: netem on the routers (only if preflight says MISSING)

On r1 and r2: temporarily add a NAT adapter, then:
```
ip -br link                      # the new interface is the NAT one
sudo ip link set <natif> up && sudo dhclient <natif>
sudo apt install -y linux-modules-extra-$(uname -r)
sudo modprobe sch_netem && echo netem-ok
```
`linux-modules-extra-$(uname -r)` matches the running kernel, so it does
not touch the kernel hold. Do not run `apt upgrade`. Shut down, remove
the NAT adapter, then boot and check with `ip -br link` that the
internal interfaces still have the same names and MACs.

Then take a powered-off snapshot of all four VMs ("ready") to return to
later.

## Step 3: Part A (after every boot)

On each VM:
```
cd /mnt/hgfs/a3
sudo bash tools/vm/setup.sh client      # r1 / r2 / server on the others
```
Once all four are done, on each VM:
```
bash tools/vm/check.sh | tee results/check-$(hostname).txt
```
Expect `ALL PINGS OK` everywhere. Traceroute on the client should go
10.10.1.1 -> 10.10.4.1 -> 10.10.3.10; on the server 10.10.3.1 ->
10.10.1.10.

Report screenshots (type these commands yourself so they show in the
shot):
- R1: Fusion's adapter settings for each VM, and `ip -br addr` and
  `ip route` on each VM.
- R2: `traceroute -n 10.10.3.10` on the client, `traceroute -n 10.10.1.10`
  on the server.
- R3: `sysctl -a 2>/dev/null | grep '\.rp_filter'` on each VM.

## Step 4: Parts C, D, E

Server VM:
```
sudo bash tools/vm/servers.sh
```
This starts `python3 -m http.server` on 8000 and our server on 8080 and
9001-9003, all serving `/var/tmp/a3/www` (fixed test files, including a
150 KB `photo.jpg`).

Client VM:
```
sudo bash tools/vm/client_tests.sh | tee results/client-tests.txt
```
Expect `ALL CLIENT TESTS PASSED`. The client builds its own identical
reference copy of the files to compare md5sums.

Screenshots for the report, typed on the client:
```
# R4: from python's server, against curl
sudo ./client/run-client http://10.10.3.10:8000/index.html -o ~/ours.html; echo "exit $?"
curl -s -o ~/curl.html http://10.10.3.10:8000/index.html
md5sum ~/ours.html ~/curl.html
#    and the same with photo.jpg
# R5: from our server: curl, wget, ours; then md5sum all three
curl -s -o ~/c.jpg http://10.10.3.10:8080/photo.jpg
wget -q -O ~/w.jpg http://10.10.3.10:8080/photo.jpg
sudo ./client/run-client http://10.10.3.10:8080/photo.jpg -o ~/o.jpg; echo "exit $?"
md5sum ~/c.jpg ~/w.jpg ~/o.jpg
```
On the server, `md5sum /var/tmp/a3/www/photo.jpg` shows the original.

## Step 5: loss and reordering (T12)

```
sudo bash tools/vm/shape.sh loss        # on r1 AND on r2
sudo bash tools/vm/client_tests.sh | tee results/client-tests-loss.txt     # client
sudo bash tools/vm/shape.sh off         # on r1 and r2 afterwards
```
Slower than step 4, but everything should still pass.

## Step 6: fairness (T14)

```
sudo bash tools/vm/shape.sh fair        # on r2 (20 Mbit/s bottleneck on Link D)
sudo bash tools/vm/fairness.sh 1 1 | tee results/fair-1-1.txt     # client
sudo bash tools/vm/fairness.sh 3 3 | tee results/fair-3-3.txt     # client
sudo bash tools/vm/shape.sh off         # on r2
```
Each run prints when each download finished. Roughly equal finish times
mean a fair share. On the replica: 1 vs 1 finished at 13.8 s and 16.7 s;
3 vs 3 between 39.5 s and 50.0 s.

## Step 7: capture for R6

```
sudo bash tools/vm/capture.sh | tee results/capture.txt     # client
cp /var/tmp/a3/conn.pcap /var/tmp/a3/capture.log results/
```
`results/conn.pcap` opens in Wireshark on the Mac. `capture.log` is our
own packet log of the same connection.

## If something fails

Copy the evidence to the shared folder and send it to me:
```
mkdir -p results/$(hostname) && sudo cp -r /var/tmp/a3/*.log /var/tmp/a3/*.err /var/tmp/a3/client results/$(hostname)/ 2>/dev/null
```
Plus the failing command's output. `results/` is git-ignored.

If running `./client/run-client` from the shared folder gives "Permission
denied" (exec bits lost), work from a local copy instead:
`cp -r /mnt/hgfs/a3 ~/a3 && cd ~/a3`, and repeat the copy after each
`git pull`.

Things that will not survive a reboot (by design): addresses, routes,
the servers, the shaping. After a reboot, redo step 3 (`setup.sh`) and
`servers.sh`.
