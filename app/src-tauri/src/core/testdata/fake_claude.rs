//! Stand-in for the `claude` CLI, compiled by the caption tests; not part of the app.
//! A real program, because Windows runs no shell scripts and Rust refuses to pass the multi-line prompt to a
//! batch file. Records its arguments in `<itself>.args`, prints `<itself>.output`, exits with `<itself>.exit`.

fn main() {
    let mut args = std::env::args();
    let me = args.next().expect("own path");
    let recorded: String = args.map(|arg| arg + "\n").collect();
    std::fs::write(format!("{me}.args"), recorded).expect("record the arguments");
    print!("{}", std::fs::read_to_string(format!("{me}.output")).expect("read the answer"));
    let code = std::fs::read_to_string(format!("{me}.exit")).expect("read the exit code");
    std::process::exit(code.parse().expect("exit code"));
}
