//! State records (init.md, "State reports"): one JSON object per line,
//! written to the console behind the prefix `ninelives-init `.
//!
//! The encoder writes ASCII only, escaping every other byte, so a record can
//! never carry a control sequence to the console. It writes into any `Out`,
//! so the panic hook can use a fixed stack buffer instead of the heap.

use std::time::Duration;

use crate::privilege::State;

/// Starts every line the init writes to the console.
pub(crate) const PREFIX: &[u8] = b"ninelives-init ";

/// Bumped when a field changes meaning or is removed.
pub(crate) const FORMAT_VERSION: u64 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum MachineState {
    Booting,
    Up,
    Emergency,
    Stopping,
}

impl MachineState {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            MachineState::Booting => "booting",
            MachineState::Up => "up",
            MachineState::Emergency => "emergency",
            MachineState::Stopping => "stopping",
        }
    }
}

/// One state change. Optional fields are left out of the line when `None`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Record {
    pub(crate) machine: MachineState,
    pub(crate) signal: Option<u32>,
    pub(crate) action: Option<&'static str>,
    pub(crate) step: Option<&'static str>,
    pub(crate) result: Option<&'static str>,
    pub(crate) error: Option<i32>,
    pub(crate) orphans_reaped: Option<u64>,
    /// What the kernel reported after the privilege drop.
    pub(crate) privilege: Option<State>,
}

impl Record {
    pub(crate) fn new(machine: MachineState) -> Record {
        Record {
            machine,
            signal: None,
            action: None,
            step: None,
            result: None,
            error: None,
            orphans_reaped: None,
            privilege: None,
        }
    }
}

/// What the sink adds to every record.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Header<'a> {
    /// The kernel's boot id, once `/proc` is mounted.
    pub(crate) boot: Option<&'a str>,
    /// `CLOCK_MONOTONIC`.
    pub(crate) mono: Duration,
    /// `CLOCK_REALTIME`; `None` before 1970, which an unset clock can show.
    pub(crate) wall: Option<Duration>,
    /// Records this sink dropped since the last one it wrote (I16).
    pub(crate) dropped: u64,
}

pub(crate) trait Out {
    fn put(&mut self, bytes: &[u8]);
}

impl Out for Vec<u8> {
    fn put(&mut self, bytes: &[u8]) {
        self.extend_from_slice(bytes);
    }
}

/// A buffer on the stack that keeps what fits and drops the rest.
pub(crate) struct FixedBuf<const N: usize> {
    buf: [u8; N],
    len: usize,
}

impl<const N: usize> FixedBuf<N> {
    pub(crate) fn new() -> Self {
        FixedBuf {
            buf: [0; N],
            len: 0,
        }
    }

    pub(crate) fn as_bytes(&self) -> &[u8] {
        self.buf.get(..self.len).unwrap_or(&[])
    }
}

impl<const N: usize> Out for FixedBuf<N> {
    fn put(&mut self, bytes: &[u8]) {
        let free = self.buf.get_mut(self.len..).unwrap_or(&mut []);
        let n = free.len().min(bytes.len());
        if let (Some(dst), Some(src)) = (free.get_mut(..n), bytes.get(..n)) {
            dst.copy_from_slice(src);
            self.len = self.len.saturating_add(n);
        }
    }
}

impl<const N: usize> std::fmt::Write for FixedBuf<N> {
    fn write_str(&mut self, s: &str) -> std::fmt::Result {
        self.put(s.as_bytes());
        Ok(())
    }
}

/// What a sink keeps between records: the boot id, the drop count, and
/// whether the last write stopped mid-line.
pub(crate) struct Sink {
    boot: Option<String>,
    buf: Vec<u8>,
    dropped: u64,
    torn: bool,
}

impl Sink {
    pub(crate) fn new() -> Sink {
        Sink {
            boot: None,
            buf: Vec::with_capacity(512),
            dropped: 0,
            torn: false,
        }
    }

    pub(crate) fn set_boot_id(&mut self, boot: Option<String>) {
        self.boot = boot;
    }

    /// Encodes `record` and hands it to `write`, one non-blocking write that
    /// returns how much it wrote, or `None` on an error. A record that does
    /// not go out whole is dropped and counted (I16), and the next one says
    /// how many. After a short write the next record starts with a newline,
    /// so it keeps its prefix and a reader can still find the ready line.
    pub(crate) fn emit(
        &mut self,
        mono: Duration,
        wall: Option<Duration>,
        record: &Record,
        write: impl FnOnce(&[u8]) -> Option<usize>,
    ) {
        self.buf.clear();
        if self.torn {
            self.buf.push(b'\n');
        }
        let header = Header {
            boot: self.boot.as_deref(),
            mono,
            wall,
            dropped: self.dropped,
        };
        encode(&mut self.buf, &header, record);
        match write(&self.buf) {
            Some(n) if n == self.buf.len() => {
                self.dropped = 0;
                self.torn = false;
            }
            Some(n) => {
                self.dropped = self.dropped.saturating_add(1);
                self.torn = self.torn || n > 0;
            }
            None => self.dropped = self.dropped.saturating_add(1),
        }
    }
}

/// Writes `ninelives-init {...}\n`. Fields come in a fixed order so a
/// reader can match on a substring such as `"machine":"up"`.
pub(crate) fn encode(out: &mut impl Out, header: &Header<'_>, record: &Record) {
    out.put(PREFIX);
    out.put(b"{\"v\":");
    put_u64(out, FORMAT_VERSION);
    out.put(b",\"boot\":");
    match header.boot {
        Some(id) => put_str(out, id.as_bytes()),
        None => out.put(b"null"),
    }
    out.put(b",\"mono_ms\":");
    put_u64(out, millis(header.mono));
    out.put(b",\"wall_ms\":");
    match header.wall {
        Some(wall) => put_u64(out, millis(wall)),
        None => out.put(b"null"),
    }
    out.put(b",\"machine\":");
    put_str(out, record.machine.as_str().as_bytes());
    if let Some(signal) = record.signal {
        out.put(b",\"signal\":");
        put_u64(out, u64::from(signal));
    }
    if let Some(action) = record.action {
        out.put(b",\"action\":");
        put_str(out, action.as_bytes());
    }
    if let Some(step) = record.step {
        out.put(b",\"step\":");
        put_str(out, step.as_bytes());
    }
    if let Some(result) = record.result {
        out.put(b",\"result\":");
        put_str(out, result.as_bytes());
    }
    if let Some(error) = record.error {
        out.put(b",\"error\":");
        put_errno(out, error);
    }
    if let Some(n) = record.orphans_reaped {
        out.put(b",\"orphans_reaped\":");
        put_u64(out, n);
    }
    if let Some(p) = &record.privilege {
        put_privilege(out, p);
    }
    if header.dropped > 0 {
        out.put(b",\"dropped\":");
        put_u64(out, header.dropped);
    }
    out.put(b"}\n");
}

/// The drop's readback. The sets are hex like `/proc/<pid>/status` shows
/// them, so a test can compare the two.
fn put_privilege(out: &mut impl Out, p: &State) {
    out.put(b",\"uid\":");
    put_ids(out, p.uid);
    out.put(b",\"gid\":");
    put_ids(out, p.gid);
    out.put(b",\"groups\":");
    put_u64(out, u64::try_from(p.groups).unwrap_or(u64::MAX));
    for (key, set) in [
        (&b",\"cap_prm\":"[..], p.permitted),
        (b",\"cap_eff\":", p.effective),
        (b",\"cap_inh\":", p.inheritable),
        (b",\"cap_amb\":", p.ambient),
        (b",\"cap_bnd\":", p.bounding),
    ] {
        out.put(key);
        put_hex64(out, set);
    }
    out.put(b",\"securebits\":");
    put_u64(out, u64::from(p.securebits));
    out.put(b",\"no_new_privs\":");
    put_u64(out, u64::from(p.no_new_privs));
    out.put(b",\"dumpable\":");
    put_u64(out, u64::from(p.dumpable));
    out.put(b",\"fsuid\":");
    put_u64(out, u64::from(p.fsuid));
    out.put(b",\"fsgid\":");
    put_u64(out, u64::from(p.fsgid));
}

fn put_ids(out: &mut impl Out, ids: [u32; 3]) {
    out.put(b"[");
    for (i, id) in ids.into_iter().enumerate() {
        if i > 0 {
            out.put(b",");
        }
        put_u64(out, u64::from(id));
    }
    out.put(b"]");
}

/// Sixteen lowercase hex digits in a string.
fn put_hex64(out: &mut impl Out, n: u64) {
    out.put(b"\"");
    for shift in (0..16u32).rev() {
        // The nibble is masked to 4 bits, so the cast cannot truncate.
        out.put(&[hex(((n >> shift.saturating_mul(4)) & 0xf) as u8)]);
    }
    out.put(b"\"");
}

/// The longest panic line: under `/dev/kmsg`'s limit for one write (1 KiB
/// less the kernel's 32-byte prefix), in case the console is that.
pub(crate) const PANIC_LINE_MAX: usize = 960;

/// The panic hook's line. It starts with a newline, since the hook cannot
/// know whether the console was left mid-line, and carries no boot id or
/// wall time: it must not touch anything that could itself fail. A long
/// message is cut so the line stays whole.
pub(crate) fn panic_line(mono: Duration, message: &[u8]) -> FixedBuf<PANIC_LINE_MAX> {
    const TAIL: &[u8] = b"\"}\n";
    let mut out = FixedBuf::new();
    out.put(b"\n");
    out.put(PREFIX);
    out.put(b"{\"v\":");
    put_u64(&mut out, FORMAT_VERSION);
    out.put(b",\"mono_ms\":");
    put_u64(&mut out, millis(mono));
    out.put(b",\"panic\":\"");
    for &b in message {
        let mut piece = FixedBuf::<6>::new();
        put_escaped(&mut piece, b);
        if out.len.saturating_add(piece.len).saturating_add(TAIL.len()) > PANIC_LINE_MAX {
            break;
        }
        out.put(piece.as_bytes());
    }
    out.put(TAIL);
    out
}

fn millis(d: Duration) -> u64 {
    u64::try_from(d.as_millis()).unwrap_or(u64::MAX)
}

fn put_u64(out: &mut impl Out, mut n: u64) {
    let mut digits = [0u8; 20];
    let mut start = digits.len();
    loop {
        start = start.saturating_sub(1);
        if let Some(d) = digits.get_mut(start) {
            // n % 10 < 10, so the cast cannot truncate.
            *d = b'0'.saturating_add((n % 10) as u8);
        }
        n /= 10;
        if n == 0 || start == 0 {
            break;
        }
    }
    out.put(digits.get(start..).unwrap_or(&[]));
}

fn hex(nibble: u8) -> u8 {
    b"0123456789abcdef"
        .get(usize::from(nibble))
        .copied()
        .unwrap_or(b'?')
}

fn put_str(out: &mut impl Out, s: &[u8]) {
    out.put(b"\"");
    for &b in s {
        put_escaped(out, b);
    }
    out.put(b"\"");
}

fn put_escaped(out: &mut impl Out, b: u8) {
    match b {
        b'"' => out.put(b"\\\""),
        b'\\' => out.put(b"\\\\"),
        0x20..=0x7e => out.put(&[b]),
        _ => out.put(&[b'\\', b'u', b'0', b'0', hex(b >> 4), hex(b & 0x0f)]),
    }
}

fn put_errno(out: &mut impl Out, errno: i32) {
    match errno_name(errno) {
        Some(name) => put_str(out, name.as_bytes()),
        None => {
            out.put(b"\"errno ");
            if errno < 0 {
                out.put(b"-");
            }
            put_u64(out, u64::from(errno.unsigned_abs()));
            out.put(b"\"");
        }
    }
}

/// The boot id as the kernel prints it (a UUID and a newline), or `None` for
/// anything else, so a record never carries bytes the kernel did not mean.
pub(crate) fn parse_boot_id(raw: &[u8]) -> Option<&str> {
    let id = raw.strip_suffix(b"\n").unwrap_or(raw);
    let well_formed = id.len() == 36
        && id.iter().enumerate().all(|(i, &b)| match i {
            8 | 13 | 18 | 23 => b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(&b),
        });
    if well_formed {
        std::str::from_utf8(id).ok()
    } else {
        None
    }
}

/// Names for the errors the init's calls can return, in Linux's generic
/// numbering (x86_64 and aarch64 share it).
pub(crate) fn errno_name(errno: i32) -> Option<&'static str> {
    Some(match errno {
        1 => "EPERM",
        2 => "ENOENT",
        3 => "ESRCH",
        4 => "EINTR",
        5 => "EIO",
        6 => "ENXIO",
        7 => "E2BIG",
        8 => "ENOEXEC",
        9 => "EBADF",
        10 => "ECHILD",
        11 => "EAGAIN",
        12 => "ENOMEM",
        13 => "EACCES",
        14 => "EFAULT",
        16 => "EBUSY",
        17 => "EEXIST",
        19 => "ENODEV",
        20 => "ENOTDIR",
        21 => "EISDIR",
        22 => "EINVAL",
        23 => "ENFILE",
        24 => "EMFILE",
        28 => "ENOSPC",
        30 => "EROFS",
        38 => "ENOSYS",
        95 => "EOPNOTSUPP",
        110 => "ETIMEDOUT",
        122 => "EDQUOT",
        _ => return None,
    })
}

#[cfg(test)]
mod tests;
