with open('tokio/src/sync/notify.rs', 'r') as f:
    text = f.read()

text = text.replace("Ordering::{self, Acquire, Relaxed, Release, SeqCst}", "Ordering::{self, AcqRel, Acquire, Relaxed, Release}")
text = text.replace(".load(SeqCst)", ".load(Acquire)")
text = text.replace(".fetch_add(1 << NOTIFY_WAITERS_SHIFT, SeqCst)", ".fetch_add(1 << NOTIFY_WAITERS_SHIFT, Release)")

text = text.replace(".compare_exchange(curr, new, SeqCst, SeqCst)", ".compare_exchange(curr, new, AcqRel, Acquire)")
text = text.replace(".compare_exchange(curr, set_state(curr, NOTIFIED), SeqCst, SeqCst)", ".compare_exchange(curr, set_state(curr, NOTIFIED), AcqRel, Acquire)")

text = text.replace(".store(new_state, SeqCst)", ".store(new_state, Release)")
text = text.replace(".store(set_state(actual, NOTIFIED), SeqCst)", ".store(set_state(actual, NOTIFIED), Release)")
text = text.replace(".store(set_state(curr, EMPTY), SeqCst)", ".store(set_state(curr, EMPTY), Release)")
text = text.replace(".store(notify_state, SeqCst)", ".store(notify_state, Release)")

# Multi-line replacements
text = text.replace("""compare_exchange(
                        set_state(curr, NOTIFIED),
                        set_state(curr, EMPTY),
                        SeqCst,
                        SeqCst,
                    )""", """compare_exchange(
                        set_state(curr, NOTIFIED),
                        set_state(curr, EMPTY),
                        AcqRel,
                        Acquire,
                    )""")

text = text.replace("""compare_exchange(
                                    set_state(curr, EMPTY),
                                    set_state(curr, WAITING),
                                    SeqCst,
                                    SeqCst,
                                )""", """compare_exchange(
                                    set_state(curr, EMPTY),
                                    set_state(curr, WAITING),
                                    AcqRel,
                                    Acquire,
                                )""")

text = text.replace("""compare_exchange(
                                    set_state(curr, NOTIFIED),
                                    set_state(curr, EMPTY),
                                    SeqCst,
                                    SeqCst,
                                )""", """compare_exchange(
                                    set_state(curr, NOTIFIED),
                                    set_state(curr, EMPTY),
                                    AcqRel,
                                    Acquire,
                                )""")


with open('tokio/src/sync/notify.rs', 'w') as f:
    f.write(text)
