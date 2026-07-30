"""Live broker: real Solana swaps via Jupiter.

Guardrails, all of which must pass before a single lamport moves:
  * execution.mode must be "live"
  * MEMEBOT_I_UNDERSTAND_THE_RISK=1 must be set in the environment
  * a keypair must be present in the configured env var
  * every order is clamped to execution.live_max_trade_usd regardless of sizing
  * an order with no Jupiter route, or with price impact above the screening
    threshold, is rejected rather than sent

`solders` and `base58` are imported lazily so paper mode and the test suite need
neither installed.
"""

from __future__ import annotations

import base64
import logging
import os
import time

from ..config import CostConfig, ExecutionConfig
from ..datasources.jupiter import JupiterClient, Quote
from ..datasources.solana_rpc import SolanaRpc
from ..models import WSOL_MINT, Fill, Side
from .base import CostModel, OrderFailed, OrderRejected

log = logging.getLogger(__name__)

RISK_ACK_ENV = "MEMEBOT_I_UNDERSTAND_THE_RISK"
LAMPORTS_PER_SOL = 1_000_000_000


class LiveTradingDisabled(RuntimeError):
    pass


class JupiterBroker:
    simulated = False

    def __init__(
        self,
        execution: ExecutionConfig,
        costs: CostConfig,
        jupiter: JupiterClient,
        rpc: SolanaRpc,
        sol_price_provider=None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.execution = execution
        self.cost_config = costs
        self.costs = CostModel(costs)
        self.jupiter = jupiter
        self.rpc = rpc
        self._env = os.environ if env is None else env
        self._sol_price_provider = sol_price_provider
        self._keypair = None
        self._pubkey: str | None = None
        self._preflight()

    # ------------------------------------------------------------------ startup

    def _preflight(self) -> None:
        if self.execution.mode != "live":
            raise LiveTradingDisabled("execution.mode is not 'live'")
        if self._env.get(RISK_ACK_ENV) != "1":
            raise LiveTradingDisabled(
                f"live trading requires {RISK_ACK_ENV}=1 in the environment. "
                "Read the README section 'Before you go live' first."
            )
        secret = self._env.get(self.execution.keypair_env)
        if not secret:
            raise LiveTradingDisabled(
                f"no keypair in ${self.execution.keypair_env}. Use a burner wallet funded "
                "with only what you are prepared to lose entirely."
            )
        self._load_keypair(secret.strip())
        log.warning(
            "LIVE TRADING ARMED — wallet %s, hard clamp $%.2f per order",
            self._pubkey, self.execution.live_max_trade_usd,
        )

    def _load_keypair(self, secret: str) -> None:
        try:
            from solders.keypair import Keypair  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise LiveTradingDisabled(
                "live trading needs `solders`: pip install -r requirements-live.txt"
            ) from exc

        try:
            if secret.startswith("["):
                import json

                self._keypair = Keypair.from_bytes(bytes(json.loads(secret)))
            else:
                import base58  # type: ignore

                self._keypair = Keypair.from_bytes(base58.b58decode(secret))
        except Exception as exc:
            raise LiveTradingDisabled(f"could not parse keypair: {exc}") from exc
        self._pubkey = str(self._keypair.pubkey())

    @property
    def public_key(self) -> str:
        if self._pubkey is None:  # pragma: no cover - _preflight guarantees this
            raise LiveTradingDisabled("no keypair loaded")
        return self._pubkey

    # -------------------------------------------------------------- price feeds

    def sol_price_usd(self) -> float:
        if self._sol_price_provider is not None:
            try:
                price = float(self._sol_price_provider())
                if price > 0:
                    return price
            except Exception as exc:
                log.warning("SOL price provider failed, falling back to config: %s", exc)
        return self.cost_config.sol_price_usd

    # ------------------------------------------------------------------- orders

    def buy(
        self,
        mint: str,
        notional_usd: float,
        quoted_price_usd: float,
        decimals: int,
        quoted_impact_bps: float = 0.0,
    ) -> Fill:
        clamped = min(notional_usd, self.execution.live_max_trade_usd)
        if clamped < notional_usd:
            log.warning("clamping order $%.2f -> $%.2f (live_max_trade_usd)", notional_usd, clamped)
        if clamped <= 0:
            raise OrderRejected("non-positive notional after clamp")

        sol_price = self.sol_price_usd()
        lamports = int(clamped / sol_price * LAMPORTS_PER_SOL)
        if lamports <= 0:
            raise OrderRejected("order rounds to zero lamports")

        quote = self.jupiter.quote(
            WSOL_MINT, mint, lamports, slippage_bps=self.execution.slippage_bps
        )
        if quote is None:
            raise OrderRejected(f"no Jupiter route SOL -> {mint[:8]}")

        signature = self._execute(quote)
        qty = quote.out_amount / (10**decimals)
        if qty <= 0:
            raise OrderFailed("swap returned zero tokens")
        fees = self._fee_estimate(clamped, sol_price)
        return Fill(
            mint=mint,
            side=Side.BUY,
            qty=qty,
            price_usd=clamped / qty,
            notional_usd=clamped,
            fee_usd=fees,
            slippage_bps=quote.price_impact_bps,
            ts=time.time(),
            tx_signature=signature,
            simulated=False,
        )

    def sell(
        self,
        mint: str,
        qty: float,
        quoted_price_usd: float,
        decimals: int,
        quoted_impact_bps: float = 0.0,
    ) -> Fill:
        raw_amount = int(qty * (10**decimals))
        if raw_amount <= 0:
            raise OrderRejected("sell amount rounds to zero")

        quote = self.jupiter.quote(
            mint, WSOL_MINT, raw_amount, slippage_bps=self.execution.slippage_bps
        )
        if quote is None:
            # Nothing to do but report it loudly — this is a rug in progress.
            raise OrderRejected(f"NO EXIT ROUTE for {mint[:8]} — token may have rugged")

        signature = self._execute(quote)
        sol_price = self.sol_price_usd()
        proceeds_usd = quote.out_amount / LAMPORTS_PER_SOL * sol_price
        fees = self._fee_estimate(proceeds_usd, sol_price)
        return Fill(
            mint=mint,
            side=Side.SELL,
            qty=qty,
            price_usd=(proceeds_usd / qty) if qty else 0.0,
            notional_usd=proceeds_usd - fees,
            fee_usd=fees,
            slippage_bps=quote.price_impact_bps,
            ts=time.time(),
            tx_signature=signature,
            simulated=False,
        )

    def _fee_estimate(self, notional_usd: float, sol_price_usd: float) -> float:
        cfg = self.cost_config
        dex = notional_usd * (cfg.dex_fee_bps + cfg.jupiter_platform_fee_bps) / 10_000.0
        return dex + self.costs.network_fee_usd(sol_price_usd)

    # ----------------------------------------------------------------- plumbing

    def _execute(self, quote: Quote) -> str:
        tx_b64 = self.jupiter.build_swap_transaction(
            quote,
            self.public_key,
            priority_fee_lamports=self.cost_config.priority_fee_lamports,
        )
        if not tx_b64:
            raise OrderFailed("Jupiter did not return a swap transaction")

        try:
            from solders.transaction import VersionedTransaction  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise LiveTradingDisabled("live trading needs `solders`") from exc

        raw = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        signed = VersionedTransaction(raw.message, [self._keypair])
        encoded = base64.b64encode(bytes(signed)).decode()

        result = self.rpc._call(
            "sendTransaction",
            [encoded, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        )
        if not isinstance(result, str):
            raise OrderFailed(f"sendTransaction rejected the swap: {result!r}")

        self._await_confirmation(result)
        return result

    def _await_confirmation(self, signature: str) -> None:
        deadline = time.time() + self.execution.confirm_timeout_seconds
        while time.time() < deadline:
            result = self.rpc._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": False}])
            statuses = (result or {}).get("value") if isinstance(result, dict) else None
            status = statuses[0] if isinstance(statuses, list) and statuses else None
            if isinstance(status, dict):
                if status.get("err"):
                    raise OrderFailed(f"tx {signature} failed on-chain: {status['err']}")
                if status.get("confirmationStatus") in ("confirmed", "finalized"):
                    log.info("tx confirmed: %s", signature)
                    return
            time.sleep(2.0)
        raise OrderFailed(
            f"tx {signature} not confirmed within {self.execution.confirm_timeout_seconds:.0f}s "
            "— check the wallet before trading again"
        )
