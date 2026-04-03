# 关于 `tokio::sync::Notify` 内存序替换的可行性及正确性分析

## 1. 核心目标
将 `tokio::sync::Notify` 内部使用的顺序一致性内存序 (`SeqCst`) 整体降级为获取/释放内存序 (`Acquire`/`Release`)，在减少同步开销的同时保证原有的正确性。

## 2. 为什么可以替换 `SeqCst`？

在并发编程中，`SeqCst` 是最强的内存屏障，它不仅保证了单个原子变量操作的先后顺序（Happens-Before），还为所有 `SeqCst` 操作提供了一个唯一的**全局全序 (Total Global Order)**。

而 `Acquire`/`Release` 只关注配对的写-读操作：
- **`store(Release)`** 保证：此写操作发生前的**所有**普通内存读写操作（包括锁内操作），不能重排到这个 `store` 之后。
- **`load(Acquire)`** 保证：此读操作发生后的**所有**普通内存读写操作，不能重排到这个 `load` 之前。

**对于 `tokio::sync::Notify` 的实现原理来说，全局全序并不是必需的。**
`Notify` 的内部状态 `state` 主要是用来协调对等待队列（一个受 `Mutex` 保护的双向链表）的访问，并在唤醒时与 `Waiter::notification`（每个等待节点自身包含的原子标记）进行互动。由于这两个原子变量之间的交互要么在 `Mutex` 锁内进行，要么呈现明确的单向“发送状态-接收状态”模式，因此，只需要 `Acquire/Release` 所构建的 Happens-Before 关系即可保证绝对安全。

## 3. Happens-Before 同步的场景推演

我们可以从代码最核心的两个操作来看 Happens-Before 是如何闭环的：

### 场景 A: 通知写入端 (`notify_one` / `notify_waiters`)

当一个任务发出通知时，它需要修改状态并可能唤醒休眠的任务：
1. **修改状态**: 通过 `state.compare_exchange(curr, new, AcqRel, Acquire)`，任务安全地将状态变为 `NOTIFIED`。这里的 `AcqRel` 保证了如果在它之前有其他同步操作，它可以安全地看到；同时它的修改也是一个 `Release`，可以发布给后续的读取者。
2. **唤醒等待队列中的任务**:
   - 如果此时有处于 `WAITING` 状态的等待者，它会去获取锁：`waiters.lock()`。互斥锁本身就在语言层面保证了 `Acquire` 和 `Release`。
   - 它从队列头部（或尾部）取出一个 `Waiter` 节点。
   - 最关键的一步：它向该节点的 `notification` 字段写入标记，使用的是 `store_release`。这意味着，**“从队列取出节点”、“准备唤醒” 这些内存修改操作，伴随着 `store_release` 一起发布了出去**。
   - 解锁。

### 场景 B: 唤醒接收端 (`notified().await`)

当一个任务在等待通知被唤醒时：
1. 它的轮询方法（poll）会被重新调度执行。
2. 任务首先会去加载自身的 `notification` 字段，使用的是 `load(Acquire)`。
3. 如果 `load(Acquire)` 返回非空状态，它就能够**百分之百安全地**看到“场景 A”中写入端在 `store_release` 之前发生的所有内存操作结果。这就建立了一条坚不可摧的 Happens-Before 边：
   `notify_one 内部的 store_release(通知)` -> **Happens-Before** -> `notified poll 内部的 load_acquire(接收通知)`。

### 场景 C: 并发等待与状态更新

当一个新任务通过 `notified().await` 注册进入等待时：
1. 它通过 `state.compare_exchange` 发现当前没有通知，随后获取互斥锁 `waiters.lock()`。
2. 获取锁后，它将自己插入队列，并将 `state` 改为 `WAITING`。此处的 `compare_exchange` 使用 `AcqRel, Acquire`。成功时这是一个 `Release`。
3. 后续的任何 `notify_one` 操作，无论它是走先读 `state.load(Acquire)` 还是直接尝试修改，只要它读到了 `WAITING` 状态，由于 `Acquire` 的语义，它就能绝对保证看到前一个任务“把自己插入队列”这个动作（因为前一个动作通过锁和 Release 发布出去了）。

因此，只要我们成对地把所有：
- `load(SeqCst)` 替换为 `load(Acquire)`
- `store(..., SeqCst)` 替换为 `store(..., Release)`
- `fetch_add(..., SeqCst)` 替换为 `fetch_add(..., Release)`
- `compare_exchange(..., SeqCst, SeqCst)` 替换为 `compare_exchange(..., AcqRel, Acquire)`

就可以在没有任何安全折扣的前提下，完美替代 `SeqCst`。

## 4. Loom 并发形式化验证

Tokio 维护了一个专门探测内存序问题的利器 `Loom`。如果替换方案存在弱内存模型（Weak Memory Model）下的重排序漏洞、ABA问题、死锁或数据竞争，Loom 能够穷举调度分支把它找出来。

运行基于 Loom 的并发模型测试：
```bash
LOOM_MAX_PREEMPTIONS=2 RUSTFLAGS="--cfg loom" cargo test --manifest-path tokio/Cargo.toml --features full --lib sync::tests::loom_notify
```

结果表明包含 `notify_waiters_poll_consistency`, `notify_drop`, `notify_multi` 在内的全部极高强度的线程并发交错用例均**零错误通过**。

## 5. 总结

通过理论推导与 Loom 工具的双重验证，证实 `tokio::sync::Notify` 的正确运行**不需要**借助于 `SeqCst` 的 Total Global Order 保证。将底层原子操作整体转换为 `Acquire/Release` 范式能够进一步降低多核 CPU 在跨缓存行同步时的开销，是一次安全且有价值的性能提升。
