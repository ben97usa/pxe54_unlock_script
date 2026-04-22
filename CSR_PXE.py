#!/usr/bin/env python3
import re
import subprocess
import sys
import os
from datetime import datetime
import pexpect

MAC_FILE = "./RM_MAC.txt"
SSH_PASSWORD = "$pl3nd1D"
FIND_IP = "/usr/local/bin/find_ip"
RSCM_SHOW_MANAGER_INFO = "show manager info"
PXE_USER = "QSITE"
PXE_IP = "192.168.202.54"
PXE_PASSWORD = "41026QMFqsi"
PXE_CSR_BASE = "/home/RMA_GPCARD/CSR"

def run(cmd):
   try:
       return subprocess.check_output(
           cmd, stderr=subprocess.STDOUT, shell=True, text=True
       )
   except subprocess.CalledProcessError as e:
       return e.output if e.output else ""
def get_mac_from_file(filepath):
   with open(filepath, "r") as f:
       return f.read().strip()
def find_ip(mac):
   output = run(f"{FIND_IP} {mac}")
   m = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
   return m.group(1) if m else None
def exec_rm_cmd(ip, cmd):
   ssh_cmd = [
       "sshpass", "-p", SSH_PASSWORD, "ssh",
       "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null",
       f"root@{ip}",
       cmd
   ]
   try:
       proc = subprocess.Popen(
           ssh_cmd,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT,
           text=True
       )
       full_output = ""
       for line in proc.stdout:
           line = line.rstrip()
           full_output += line + "\n"
       proc.wait()
       return "Completion Code: Success" in full_output, full_output
   except Exception as e:
       print(f"SSH execution failed: {e}")
       return False, ""
def exec_cmd(ip, slot, action, extra=None):
   if action == "gp_info":
       cmd = f"show system info -i {slot} -b 1"
   else:
       cmd = f"set system cmd -i {slot} -c {extra}"
   ssh_cmd = [
       "sshpass", "-p", SSH_PASSWORD, "ssh",
       "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null",
       f"root@{ip}",
       cmd
   ]
   try:
       proc = subprocess.Popen(
           ssh_cmd,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT,
           text=True
       )
       full_output = ""
       for line in proc.stdout:
           line = line.rstrip()
           full_output += line + "\n"
           if "Completion Code:" in line:
               print(line)
       proc.wait()
       return "Completion Code: Success" in full_output, full_output
   except Exception as e:
       print(f"SSH execution failed: {e}")
       return False, ""
def get_server_slots(rm_manager_info_output):
   server_slots = []
   for line in rm_manager_info_output.splitlines():
       line = line.strip()
       if not line.startswith("|"):
           continue
       if "Port State" in line:
           continue
       parts = [p.strip() for p in line.split("|") if p.strip()]
       if len(parts) < 7:
           continue
       slot = parts[0]
       port_type = parts[3]
       completion_code = parts[6]
       if port_type == "Server" and completion_code == "Success":
           server_slots.append(int(slot))
   return server_slots
def extract_board_serial(output):
   for line in output.splitlines():
       if "Board Serial" in line:
           return line.split(":", 1)[1].strip()
   return None
def get_today_folder():
   now = datetime.now()
   return now.strftime("%B") + str(now.day)
def ensure_pxe_day_folder(day_folder):
   dest_dir = f"{PXE_CSR_BASE}/{day_folder}"
   cmd = f"mkdir -p '{dest_dir}'"
   result = run(cmd)
   if os.path.isdir(dest_dir):
       print(f"Ready folder: {dest_dir}")
       return dest_dir
   print(f"Failed to create folder: {dest_dir}")
   print(result)
   return None
def gp_login(ip, slot):
   child = pexpect.spawn(
       f"sshpass -p '{SSH_PASSWORD}' ssh -o StrictHostKeyChecking=no root@{ip}",
       encoding="utf-8",
       timeout=40
   )
   child.logfile = sys.stdout
   child.expect(r"#")
   child.sendline(f"start serial session -i {slot} -p 8295")
   child.sendline("")
   child.expect(r"root@localhost:", timeout=40)
   print(f"Entered GP Console for slot {slot}")
   return child
def gp_exit(child):
   print("Exiting both GP card & RSCM")
   child.send("~.")
   child.expect(pexpect.EOF, timeout=10)
   child.close()
def gp_collect_csr_only(child, gp_sn):
   csr_dir = f"/tmp/{gp_sn}"
   csr_file = f"{csr_dir}/{gp_sn}.CSR"
   child.sendline(f"ls {csr_file}")
   child.expect(r"root@localhost:", timeout=20)
   output = child.before
   if gp_sn + ".CSR" in output:
       print(f"Found CSR: {csr_file}")
       return True, csr_file
   print(f"CSR not found: {csr_file}")
   return False, csr_file
def gp_scp_csr_to_pxe(child, gp_sn, dest_dir):
   csr_file = f"/tmp/{gp_sn}/{gp_sn}.CSR"
   # make sure destination exists
   child.sendline(f"scp -o StrictHostKeyChecking=no {csr_file} {PXE_USER}@{PXE_IP}:{dest_dir}/")
   idx = child.expect(
       [
           r"yes/no",
           r"[Pp]assword:",
           r"root@localhost:",
           pexpect.TIMEOUT,
           pexpect.EOF,
       ],
       timeout=60
   )
   if idx == 0:
       child.sendline("yes")
       idx = child.expect([r"[Pp]assword:", r"root@localhost:", pexpect.TIMEOUT], timeout=30)
       if idx == 0:
           child.sendline(PXE_PASSWORD)
           child.expect(r"root@localhost:", timeout=120)
           output = child.before
       elif idx == 1:
           output = child.before
       else:
           print(f"SCP timeout for {gp_sn}")
           return False
   elif idx == 1:
       child.sendline(PXE_PASSWORD)
       child.expect(r"root@localhost:", timeout=120)
       output = child.before
   elif idx == 2:
       output = child.before
   else:
       print(f"SCP failed for {gp_sn}")
       return False
   
   # verify file really exists on PXE
   # 
   verify_cmd = f"sshpass -p {PXE_PASSWORD} ssh -o StrictHostKeyChecking=no {PXE_USER}@{PXE_IP} \"test -f '{dest_dir}/{gp_sn}.CSR' && echo OK || echo FAIL\""
   verify_result = run(verify_cmd).strip()   
   if verify_result == "OK":
       print(f"SCP success: {dest_dir}/{gp_sn}.CSR")
       return True
   print(f"SCP unsuccessful for {gp_sn}")
   print(output)
   return False
def main():
   rm_mac = get_mac_from_file(MAC_FILE)
   print(f"Using RM MAC: {rm_mac}")
   ip = find_ip(rm_mac)
   if not ip:
       print("Failed to find RM IP from RM_MAC.txt")
       return
   print(f"RM IP: {ip}")
   day_folder = get_today_folder()
   dest_dir = ensure_pxe_day_folder(day_folder)
   if not dest_dir:
       return
   cmd_succeed, rm_manager_info = exec_rm_cmd(ip, RSCM_SHOW_MANAGER_INFO)
   if not cmd_succeed:
       print(f"Failed to get manager info from RM at {rm_mac} ({ip})")
       return
   slots = get_server_slots(rm_manager_info)
   print(f"Server slots at {rm_mac} ({ip}): {slots}")
   success_list = []
   failed_list = []
   for slot in slots:
       print(f"\n=== Processing slot {slot} ===")
       success, gp_fru_output = exec_cmd(ip, slot, "cmd", "fru print 2")
       if not success:
           print(f"Failed to get FRU for slot {slot}")
           failed_list.append((slot, "FRU failed"))
           continue
       gp_sn = extract_board_serial(gp_fru_output)
       if not gp_sn:
           print(f"Could not get Board Serial for slot {slot}")
           failed_list.append((slot, "No Board Serial"))
           continue
       print(f"GP SN: {gp_sn}")
       gp_child = None
       try:
           gp_child = gp_login(ip, slot)
           csr_ok, csr_file = gp_collect_csr_only(gp_child, gp_sn)
           if not csr_ok:
               print(f"False - slot {slot} - {gp_sn} - CSR not found")
               failed_list.append((slot, f"{gp_sn} CSR not found"))
               gp_exit(gp_child)
               continue
           scp_ok = gp_scp_csr_to_pxe(gp_child, gp_sn, dest_dir)
           if scp_ok:
               success_list.append((slot, gp_sn))
               print(f"Successful - slot {slot} - {gp_sn}")
           else:
               failed_list.append((slot, f"{gp_sn} SCP unsuccessful"))
               print(f"Unsuccessful - slot {slot} - {gp_sn}")
           gp_exit(gp_child)
       except Exception as e:
           print(f"Exception on slot {slot}: {e}")
           failed_list.append((slot, f"Exception: {e}"))
           try:
               if gp_child is not None:
                   gp_exit(gp_child)
           except:
               pass
   print("\n==============================")
   print("Finished Collecting CSRs")
   print("==============================")
   print(f"Destination folder: {dest_dir}")
   print(f"Successful: {len(success_list)}")
   for slot, gp_sn in success_list:
       print(f"  slot {slot}: {gp_sn}")
   print(f"Failed: {len(failed_list)}")
   for slot, reason in failed_list:
       print(f"  slot {slot}: {reason}")
if __name__ == "__main__":
   main()
