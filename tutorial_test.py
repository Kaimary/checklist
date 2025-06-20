# %%
import checklist
from checklist.editor import Editor
from checklist.perturb import Perturb
from checklist.test_types import MFT, INV, DIR

# %% [markdown]
# For this tutorial, we will assume that our task is sentiment analysis.

# %%
editor = Editor()

# %% [markdown]
# ## Minimum Functionality Test (MFT)

# %% [markdown]
# A Minimum Functionality Test is like a unit test in Software Engineering.
# If you are testing a certain capability (e.g. 'can the model handle negation?'), an MFT is composed of simple examples that verify a specific behavior.  
# Let's create a very simple MFT for negations:

# %%
# First, let's find some positive and negative adjectives
', '.join(editor.suggest('This is not {a:mask} {thing}.', thing=['book', 'movie', 'show', 'game'])[:30])

# %%
pos = ['good', 'enjoyable', 'exciting', 'excellent', 'amazing', 'great', 'engaging']
neg = ['bad', 'terrible', 'awful', 'horrible']

# %% [markdown]
# Now let's create some data with both positive and negative negations, assuming `1` means positive and `0` means negative:

# %%
ret = editor.template('This is not {a:pos} {mask}.', pos=pos, labels=0, save=True, nsamples=100)
ret += editor.template('This is not {a:neg} {mask}.', neg=neg, labels=1, save=True, nsamples=100)

# %% [markdown]
# We can easily turn this data into an MFT:

# %%
test = MFT(ret.data, labels=ret.labels, name='Simple negation',
           capability='Negation', description='Very simple negations.')

# %% [markdown]
# Since `ret` is a dict where keys have the right names for test arguments, we can also use a simpler call:

# %%
test = MFT(**ret, name='Simple negation',
           capability='Negation', description='Very simple negations.')

# %% [markdown]
# ### Running tests

# %% [markdown]
# Let's use an off-the-shelf sentiment analysis model.

# %%
from pattern.en import sentiment

# %%
import numpy as np
def predict_proba(inputs):
    p1 = np.array([(sentiment(x)[0] + 1)/2. for x in inputs]).reshape(-1, 1)
    p0 = 1- p1
    return np.hstack((p0, p1))

# %%
# Predictions are random
predict_proba(['good', 'bad'])

# %% [markdown]
# There are two ways of running tests.  
# In the first (and simplest) way, you pass a function as argument to `test.run`, which gets called to make predictions.  
# We assume that the function returns a tuple with `(predictions, confidences)`, so we have a wrapper to turn softmax (like our function above) into this:

# %%
from checklist.pred_wrapper import PredictorWrapper
wrapped_pp = PredictorWrapper.wrap_softmax(predict_proba)

# %%
wrapped_pp(['good'])

# %% [markdown]
# Once you have this function, running the test is as simple as calling `test.run`.  
# You can run the test on a subset of testcases (for speed's sake) by specifying `n` if needed.  
# We won't do that here since our test is small)

# %%
test.run(wrapped_pp)

# %% [markdown]
# Once you run a test, you can print a summary of the results with `test.summary()`

# %%
test.summary()

# %% [markdown]
# It seems that this off-the-shelf system has trouble with negation.
# Note the failures: examples that should be negative are predicted as positive and vice versa (the number shown is the probability of positive)

# %% [markdown]
# If you are using jupyter notebooks, you can use `test.visual_summary()` for a nice visualization version of these results:  
# (I'll load a gif so you can see this in preview mode)

# %%
# from IPython.display import HTML, Image
# with open('visual_summary.gif','rb') as f:
#     display(Image(data=f.read(), format='png'))
test.visual_summary()

# %% [markdown]
# The second way to run a test is from a prediction file.  
# First, we export the test into a text file:

# %%
test.to_raw_file('/tmp/raw_file.txt')

# %% [markdown]
# Then, you get predictions from the examples in the raw file (in order) however you want, and save them in a prediction file.  
# Let's simulate this process here:

# %%
docs = open('/tmp/raw_file.txt').read().splitlines()
preds = predict_proba(docs)
f = open('/tmp/softmax_preds.txt', 'w')
for p in preds:
    f.write('%f %f\n' % tuple(p))
f.close()

# %% [markdown]
# We can run the test from this file.  
# We have to specify the file format (see the API for possible choices), or a function that takes a line in the file and outputs predictions and confidences.  
# Since we had already run this test, we have to set `overwrite=True` to overwrite the previous results.

# %%
test.run_from_file('/tmp/softmax_preds.txt', file_format='softmax', overwrite=True)

# %%
test.summary()

# %% [markdown]
# ## Invariance tests

# %% [markdown]
# An Invariance test (INV) is when we apply label-preserving perturbations to inputs and expect the model prediction to remain the same.  
# Let's start by creating a fictitious dataset to serve as an example, and process it with spacy

# %%
import spacy
nlp = spacy.load("en_core_web_sm")

# %%
dataset = ['This was a very nice movie directed by John Smith.',
           'Mary Keen was brilliant.', 
          'I hated everything about this.',
          'This movie was very bad.',
          'I really liked this movie.',
          'just bad.',
          'amazing.',
          ]
pdataset = list(nlp.pipe(dataset))

# %% [markdown]
# Now let's apply a simple perturbation: changing people's names and expecting predictions to remain the same:

# %%
t = Perturb.perturb(pdataset, Perturb.change_names)
print('\n'.join(t.data[0][:3]))
print('...')
test = INV(**t)

# %%
test.run(wrapped_pp)
test.summary()

# %% [markdown]
# Let's try a different test: adding typos and expecting predictions to remain the same

# %%
t = Perturb.perturb(dataset, Perturb.add_typos)
print('\n'.join(t.data[0][:3]))
print('...')
test = INV(**t)

# %%
test.run(wrapped_pp)
test.summary()

# %% [markdown]
# ## Directional Expectation tests

# %% [markdown]
# A Directional Expectation test (DIR) is just like an INV, in the sense that we apply a perturbation to existing inputs. However, instead of expecting invariance, we expect the model to behave in a some specified way.

# %% [markdown]
# For example, let's start with a very simple perturbation: we'll add very negative phrases to the end of our small dataset:

# %%
def add_negative(x):
    phrases = ['Anyway, I thought it was bad.', 'Having said this, I hated it', 'The director should be fired.']
    return ['%s %s' % (x, p) for p in phrases]

# %%
dataset[0], add_negative(dataset[0])

# %% [markdown]
# What would we expect after this perturbation? I think the least we should expect is that the prediction probability of positive should **not go up** (that is, it should be monotonically decreasing).  
# Monotonicity is an expectation function that is built in, so we don't need to implement it.
# `tolerance=0.1` means we won't consider it a failure if the prediction probability goes up by less than 0.1, only if it goes up by more

# %%
from checklist.expect import Expect

# %%
monotonic_decreasing = Expect.monotonic(label=1, increasing=False, tolerance=0.1)

# %%
t = Perturb.perturb(dataset, add_negative)
test = DIR(**t, expect=monotonic_decreasing)

# %%
test.run(wrapped_pp)
test.summary()

# %% [markdown]
# #### Writing custom expectation functions

# %% [markdown]
# If you are writing a custom expectation functions, it must return a float or bool for each example such that:
# - `> 0` (or True) means passed,
# - `<= 0` or False means fail, and (optionally) the magnitude of the failure, indicated by distance from 0, e.g. -10 is worse than -1
# - `None` means the test does not apply, and this should not be counted
# 
# Each test case can have multiple examples. In our MFTs, each test case only had a single example, but in our INVs and DIRs, they had multiple examples (e.g. we changed people's names to various other names).
# 
# You can write custom expectation functions at multiple levels of granularity.  
# 

# %% [markdown]
# #### Expectation on a single example
# 
# If you want to write an expectation function that acts on each individual example, you write a function with the following signature:
# 
# `def fn(x, pred, conf, label=None, meta=None):`
# 
# For example, let's write a (useless) expectation function that checks that every prediction confidence is higher than 0.95:

# %%
# Function that expects prediction confidence to always be more than 0.9
def high_confidence(x, pred, conf, label=None, meta=None):
    return conf.max() > 0.95

# %% [markdown]
# We then wrap this function with `Expect.single`, and apply it to our previous test to see the result:

# %%
expect_fn = Expect.single(high_confidence)

# %%
test.set_expect(expect_fn)
test.summary()

# %% [markdown]
# Notice that every test case fails now: there is always some prediction in it that has confidence smaller than 0.95.  
# By default, the way we aggregate all results in a test case is such that the testcase fails if **any** examples in it fail (for MFTs), or **any but the first** fail for INVs and DIRs (because the first is usually the original data point before perturbation). You can change these defaults with the `agg_fn` argument.

# %% [markdown]
# #### Expectation on  pairs 
# 
# Most of the time for DIRs, you want to write an expectation function that acts on pairs of `(original, new)` examples - that is, the original example and the perturbed examples. If this is the case, the signature is as follows:
# 
# `def fn(orig_pred, pred, orig_conf, conf, labels=None, meta=None)`
# 
# For example, let's write an expectation function that checks that the prediction **changed** after applying the perturbation, and wrap it with `Expect.pairwise`:

# %%
def changed_pred(orig_pred, pred, orig_conf, conf, labels=None, meta=None):
    return pred != orig_pred
expect_fn = Expect.pairwise(changed_pred)

# %% [markdown]
# Let's actually create a new test where we add negation to our dataset:

# %%
t = Perturb.perturb(pdataset, Perturb.add_negation)
t.data[0:2]

# %%
test = DIR(**t, expect=expect_fn)
test.run(wrapped_pp)
test.summary()

# %% [markdown]
# Note the failure: prediction did not change after adding negation.

# %% [markdown]
# You can write much more complex expectation functions, but these are enough for this tutorial.  
# You can check out `expect.py` or the notebooks for Sentiment Analysis, QQP and SQuAD for many additional examples.


