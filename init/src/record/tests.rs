use super::*;

const BOOT: &str = "0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10";

fn header(dropped: u64) -> Header<'static> {
    Header {
        boot: Some(BOOT),
        mono: Duration::from_millis(1234),
        wall: Some(Duration::from_millis(1_763_500_000_000)),
        dropped,
    }
}

fn line(header: &Header<'_>, record: &Record) -> String {
    let mut out = Vec::new();
    encode(&mut out, header, record);
    String::from_utf8(out).unwrap()
}

#[test]
fn the_ready_line_is_exact() {
    // The qemu boot check (#2) waits for this line; changing it breaks that check.
    assert_eq!(
        line(&header(0), &Record::new(MachineState::Up)),
        "ninelives-init {\"v\":1,\"boot\":\"0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10\",\
         \"mono_ms\":1234,\"wall_ms\":1763500000000,\"machine\":\"up\"}\n"
    );
}

#[test]
fn optional_fields_appear_in_a_fixed_order() {
    let record = Record {
        signal: Some(12),
        action: Some("poweroff"),
        step: Some("reboot"),
        result: Some("done"),
        error: Some(22),
        orphans_reaped: Some(3),
        ..Record::new(MachineState::Stopping)
    };
    assert_eq!(
        line(&header(4), &record),
        "ninelives-init {\"v\":1,\"boot\":\"0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10\",\
         \"mono_ms\":1234,\"wall_ms\":1763500000000,\"machine\":\"stopping\",\
         \"signal\":12,\"action\":\"poweroff\",\"step\":\"reboot\",\"result\":\"done\",\
         \"error\":\"EINVAL\",\"orphans_reaped\":3,\"dropped\":4}\n"
    );
}

#[test]
fn unknown_boot_and_wall_time_are_null() {
    let h = Header {
        boot: None,
        mono: Duration::ZERO,
        wall: None,
        dropped: 0,
    };
    assert_eq!(
        line(&h, &Record::new(MachineState::Booting)),
        "ninelives-init {\"v\":1,\"boot\":null,\"mono_ms\":0,\"wall_ms\":null,\
         \"machine\":\"booting\"}\n"
    );
}

#[test]
fn a_panic_line_is_one_ascii_line_on_a_line_of_its_own() {
    let line = panic_line(
        Duration::from_secs(1),
        "bad\n\"thing\"\\\u{7f}\u{1b}[2J é".as_bytes(),
    );
    let s = std::str::from_utf8(line.as_bytes()).unwrap();
    assert!(s.is_ascii());
    assert!(s.starts_with("\nninelives-init {\"v\":1,\"mono_ms\":1000,"));
    assert_eq!(s.matches('\n').count(), 2);
    assert!(s.ends_with("}\n"));
    assert!(s.contains(r#""panic":"bad\u000a\"thing\"\\\u007f\u001b[2J \u00c3\u00a9""#));
}

#[test]
fn a_long_panic_line_is_cut_and_stays_whole() {
    let message = vec![0x01u8; 10_000];
    let line = panic_line(Duration::from_secs(1), &message);
    let s = std::str::from_utf8(line.as_bytes()).unwrap();
    assert!(s.len() <= PANIC_LINE_MAX);
    assert!(s.len() > PANIC_LINE_MAX - 6, "cut too early: {}", s.len());
    assert!(s.ends_with("\"}\n"));
    // Never half an escape.
    let body = &s[s.find("\"panic\":\"").unwrap() + 9..s.len() - 3];
    assert_eq!(body.len() % 6, 0);
}

#[test]
fn unknown_errno_is_numbered() {
    let record = Record {
        error: Some(4095),
        ..Record::new(MachineState::Emergency)
    };
    assert!(line(&header(0), &record).contains(r#""error":"errno 4095""#));
    let record = Record {
        error: Some(-7),
        ..Record::new(MachineState::Emergency)
    };
    assert!(line(&header(0), &record).contains(r#""error":"errno -7""#));
}

#[test]
fn numbers_cover_the_u64_range() {
    for n in [0, 9, 10, 99, 100, 12345, u64::MAX] {
        let mut out = Vec::new();
        put_u64(&mut out, n);
        assert_eq!(String::from_utf8(out).unwrap(), n.to_string());
    }
}

#[test]
fn a_fixed_buffer_truncates_instead_of_failing() {
    let mut buf = FixedBuf::<8>::new();
    buf.put(b"0123");
    buf.put(b"456789");
    assert_eq!(buf.as_bytes(), b"01234567");
    buf.put(b"x");
    assert_eq!(buf.as_bytes(), b"01234567");
}

#[cfg(target_os = "linux")]
#[test]
fn errno_names_match_the_target() {
    for (n, name) in [
        (libc::EPERM, "EPERM"),
        (libc::ENOENT, "ENOENT"),
        (libc::ESRCH, "ESRCH"),
        (libc::EINTR, "EINTR"),
        (libc::EIO, "EIO"),
        (libc::ENXIO, "ENXIO"),
        (libc::E2BIG, "E2BIG"),
        (libc::ENOEXEC, "ENOEXEC"),
        (libc::EBADF, "EBADF"),
        (libc::ECHILD, "ECHILD"),
        (libc::EAGAIN, "EAGAIN"),
        (libc::ENOMEM, "ENOMEM"),
        (libc::EACCES, "EACCES"),
        (libc::EFAULT, "EFAULT"),
        (libc::EBUSY, "EBUSY"),
        (libc::EEXIST, "EEXIST"),
        (libc::ENODEV, "ENODEV"),
        (libc::ENOTDIR, "ENOTDIR"),
        (libc::EISDIR, "EISDIR"),
        (libc::EINVAL, "EINVAL"),
        (libc::ENFILE, "ENFILE"),
        (libc::EMFILE, "EMFILE"),
        (libc::ENOSPC, "ENOSPC"),
        (libc::EROFS, "EROFS"),
        (libc::ENOSYS, "ENOSYS"),
        (libc::EOPNOTSUPP, "EOPNOTSUPP"),
        (libc::ETIMEDOUT, "ETIMEDOUT"),
        (libc::EDQUOT, "EDQUOT"),
    ] {
        assert_eq!(errno_name(n), Some(name));
    }
}

#[test]
fn boot_id_must_be_a_lowercase_uuid() {
    assert_eq!(parse_boot_id(format!("{BOOT}\n").as_bytes()), Some(BOOT));
    assert_eq!(parse_boot_id(BOOT.as_bytes()), Some(BOOT));
    for bad in [
        "",
        "\n",
        "0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d1",
        "0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d100",
        "0F6C2A3E-6D3C-4C58-9C55-6A4B1F3E2D10",
        "0f6c2a3e_6d3c-4c58-9c55-6a4b1f3e2d10",
        "0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d1\"",
        "0f6c2a3e-6d3c-4c58-9c55-6a4b1f3e2d10\n\n",
    ] {
        assert_eq!(parse_boot_id(bad.as_bytes()), None, "{bad:?}");
    }
}

fn emit(sink: &mut Sink, accept: Option<usize>, out: &mut Vec<u8>) {
    sink.emit(
        Duration::ZERO,
        None,
        &Record::new(MachineState::Up),
        |bytes| {
            let n = accept.map(|a| a.min(bytes.len()));
            if let Some(n) = n {
                out.extend_from_slice(&bytes[..n]);
            }
            n
        },
    );
}

#[test]
fn drops_are_counted_and_reported_by_the_next_record() {
    let mut sink = Sink::new();
    let mut out = Vec::new();
    emit(&mut sink, None, &mut out);
    emit(&mut sink, Some(0), &mut out);
    assert!(out.is_empty());
    emit(&mut sink, Some(usize::MAX), &mut out);
    let s = String::from_utf8(out).unwrap();
    assert!(s.starts_with("ninelives-init {"));
    assert!(s.ends_with(",\"dropped\":2}\n"), "{s}");
    // The count resets once it has been reported.
    let mut out = Vec::new();
    emit(&mut sink, Some(usize::MAX), &mut out);
    assert!(!String::from_utf8(out).unwrap().contains("dropped"));
}

#[test]
fn a_record_after_a_short_write_starts_on_its_own_line() {
    let mut sink = Sink::new();
    let mut out = Vec::new();
    emit(&mut sink, Some(10), &mut out);
    // A failed write in between keeps the line torn.
    emit(&mut sink, None, &mut out);
    emit(&mut sink, Some(usize::MAX), &mut out);
    let s = String::from_utf8(out).unwrap();
    let lines: Vec<&str> = s.lines().collect();
    assert_eq!(lines.len(), 2, "{s}");
    assert_eq!(lines[0], "ninelives-");
    assert!(lines[1].starts_with("ninelives-init {"));
    assert!(lines[1].contains(r#""machine":"up""#));
    assert!(lines[1].ends_with(r#""dropped":2}"#));
    // Whole again, so no leading newline next time.
    let mut out = Vec::new();
    emit(&mut sink, Some(usize::MAX), &mut out);
    assert!(out.starts_with(b"ninelives-init {"));
}
