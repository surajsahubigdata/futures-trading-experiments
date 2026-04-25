# Monte Carlo simulation for BTC/USDT futures position
# This code simulates price paths and estimates probability of liquidation and P&L outcomes.
# It handles both inferred short or long positions based on liquidation vs mark price.
# The simulation runs multiple volatility scenarios to show sensitivity.
#
# Inputs (from user):
positions_btc = 0.08
margin_usdt = 187.0
entry_price = 116900.0
mark_price = 123000.0
liquidation_price = 136000.0
horizon_days = 30 # How far into the future am I stress-testing this position?
n_sims = 10000 # total number of simulations
daily_steps = 1  # daily steps; How many simulation time intervals (steps) you divide each day into.
seed = 42 # The random seed is a number used to initialize the random number generator that produces the random price paths in your Monte Carlo simulation.

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
#from caas_jupyter_tools import display_dataframe_to_user

np.random.seed(seed)

# Infer position direction: if liquidation_price > mark_price, assume SHORT (liquidation on price rise).
# Otherwise assume LONG (liquidation on price fall).
if liquidation_price > mark_price:
    position_side = "SHORT"
    liquid_condition = lambda p: p >= liquidation_price
else:
    position_side = "LONG"
    liquid_condition = lambda p: p <= liquidation_price

# Compute notional and effective leverage
notional_usdt = positions_btc * mark_price
effective_leverage = notional_usdt / margin_usdt

# Scenarios for daily volatility (std dev of daily returns): 2%, 4%, 8% (conservative -> high vol)
vol_scenarios = [0.02, 0.04, 0.08]
results = []

for sigma in vol_scenarios:
    sims_final_equity = np.zeros(n_sims)
    sims_time_to_liquid = np.full(n_sims, np.nan)
    sims_liquid_flag = np.zeros(n_sims, dtype=bool)
    sims_final_price = np.zeros(n_sims)
    sims_final_pnl = np.zeros(n_sims)
    
    for i in range(n_sims):
        # simulate daily returns using geometric Brownian motion with zero drift (mu=0)
        steps = horizon_days * daily_steps
        mu = 0.0
        dt = 1.0 / daily_steps
        # generate returns as normal with mean mu*dt and sd sigma*sqrt(dt)
        daily_ret = np.random.normal(loc=mu*dt, scale=sigma*np.sqrt(dt), size=steps)
        # price path
        prices = mark_price * np.exp(np.cumsum(np.log1p(daily_ret)))  # approximate
        # check liquidation
        liquid_idx = None
        for t, p in enumerate(prices):
            if liquid_condition(p):
                liquid_idx = t
                break
        if liquid_idx is not None:
            sims_liquid_flag[i] = True
            sims_time_to_liquid[i] = liquid_idx + 1  # days
            sims_final_price[i] = prices[liquid_idx]
            sims_final_pnl[i] = positions_btc * (sims_final_price[i] - entry_price)
            sims_final_equity[i] = 0.0  # assume liquidation wipes margin (approx)
        else:
            sims_final_price[i] = prices[-1]
            sims_final_pnl[i] = positions_btc * (sims_final_price[i] - entry_price)
            sims_final_equity[i] = margin_usdt + sims_final_pnl[i]
    
    # stats
    prob_liquid = sims_liquid_flag.mean()
    median_final_equity = np.median(sims_final_equity)
    pct_left_positive = (sims_final_equity > 0).mean()
    avg_days_to_liquid = np.nanmean(sims_time_to_liquid)
    avg_pnl = np.mean(sims_final_pnl)
    pct_liquid_within_7 = np.mean(sims_time_to_liquid <= 7) if np.any(~np.isnan(sims_time_to_liquid)) else 0.0
    
    results.append({
        "daily_vol": sigma,
        "prob_liquid_30d": prob_liquid,
        "prob_liquid_within_7d": pct_liquid_within_7,
        "median_final_equity": median_final_equity,
        "avg_final_pnl_usdt": avg_pnl,
        "pct_positive_equity": pct_left_positive,
        "avg_days_to_liquid": avg_days_to_liquid,
        "effective_leverage": effective_leverage,
        "position_side": position_side,
        "notional_usdt": notional_usdt
    })
    
    # plot sample of price paths and show liquidation threshold
    plt.figure(figsize=(8,4))
    sample_idx = np.random.choice(n_sims, size=200, replace=False)
    for j in sample_idx:
        # re-simulate for plotting clarity (reuse same distribution)
        ret = np.random.normal(loc=0.0, scale=sigma, size=horizon_days)
        path = mark_price * np.cumprod(1 + ret)
        plt.plot(np.arange(1, horizon_days+1), path, alpha=0.15)
    plt.title(f"Sample price paths (daily vol={sigma*100:.1f}%) - inferred {position_side}")
    plt.xlabel("Days")
    plt.ylabel("Price (USDT)")
    plt.axhline(y=liquidation_price, linestyle='--')
    plt.tight_layout()
    plt.show()

# Display results
df_results = pd.DataFrame(results)
print("\n--- Monte Carlo Summary ---\n")
print(df_results)

# Print a concise summary
print("INPUT SUMMARY")
print("----------------")
print(f"Position: {positions_btc} BTC ({position_side})")
print(f"Margin (USDT): {margin_usdt:.2f}")
print(f"Entry price: {entry_price:.2f}, Mark price: {mark_price:.2f}, Liquidation price: {liquidation_price:.2f}")
print(f"Notional: {notional_usdt:.2f} USDT, Effective leverage: {effective_leverage:.2f}x\n")

for r in results:
    print(f"Scenario: daily vol {r['daily_vol']*100:.1f}%")
    print(f" - Probability of liquidation within {horizon_days} days: {r['prob_liquid_30d']*100:.2f}%")
    print(f" - Probability of liquidation within 7 days: {r['prob_liquid_within_7d']*100:.2f}%")
    print(f" - Median final equity after {horizon_days}d: {r['median_final_equity']:.2f} USDT")
    print(f" - Avg final PnL (USDT): {r['avg_final_pnl_usdt']:.2f}")
    print(f" - % sims with equity > 0 at horizon: {r['pct_positive_equity']*100:.2f}%")
    print(f" - Avg days to liquidation (if liquidated): {r['avg_days_to_liquid']:.2f}\n")
