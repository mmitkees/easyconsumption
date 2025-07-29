import oci
import time

def choose_from_list(items, attr, prompt):
    """
    Helper function to present a numbered list of options for the user to choose from.
    items: list of objects
    attr: attribute of the object to display (e.g. 'display_name')
    prompt: prompt text for input
    Returns: the selected object
    """
    for idx, item in enumerate(items):
        print(f"[{idx}] {getattr(item, attr)}")
    choice = input(f"{prompt} [0-{len(items)-1}] (default 0): ").strip()
    idx = int(choice) if choice.isdigit() and int(choice) < len(items) else 0
    return items[idx]

# === OCI SDK Setup ===
# Load your Cloud Shell OCI config and create service clients
config = oci.config.from_file()
identity = oci.identity.IdentityClient(config)
core = oci.core.ComputeClient(config)
network = oci.core.VirtualNetworkClient(config)

# === 1. Get your tenancy OCID from config ===
tenancy_id = config["tenancy"]
print(f"Tenancy OCID: {tenancy_id}")

# === 2. Find the root compartment (your tenancy) ===
# We'll use the root compartment to find VCNs, subnets, etc.
compartments = identity.list_compartments(tenancy_id, compartment_id_in_subtree=True, access_level="ANY").data
root_compartment = [c for c in compartments if c.id == tenancy_id or c.name == "root"]
if not root_compartment:
    print("Root compartment not found! Exiting.")
    exit(1)
root_compartment_id = tenancy_id

# === 3. List VCNs and subnets, and let user pick one ===
vnets = network.list_vcns(root_compartment_id).data
if not vnets:
    print("No VCNs found in root compartment! Exiting.")
    exit(1)
vcn = choose_from_list(vnets, 'display_name', "Select VCN")
subnets = network.list_subnets(root_compartment_id, vcn_id=vcn.id).data
if not subnets:
    print("No subnets found in selected VCN! Exiting.")
    exit(1)
subnet = choose_from_list(subnets, 'display_name', "Select Subnet")
subnet_id = subnet.id
print(f"Selected Subnet OCID: {subnet_id}")

# === 4. List availability domains and let user pick one ===
ads = identity.list_availability_domains(tenancy_id).data
ad = choose_from_list(ads, 'name', "Select Availability Domain")
ad_name = ad.name
print(f"Selected Availability Domain: {ad_name}")

# === 5. List all Windows images for the required shape and pick one ===
images = core.list_images(
    compartment_id=root_compartment_id,
    operating_system="Windows",
    shape="VM.Standard3.Flex"
).data
if not images:
    print("No Windows images found for this shape! Exiting.")
    exit(1)
# Sort images by creation date (descending) to present the newest first
images.sort(key=lambda x: x.time_created, reverse=True)
win_image = choose_from_list(images, 'display_name', "Select Windows Image")
win_image_id = win_image.id
print(f"Selected Windows Image OCID: {win_image_id}")

# === 6. Create a new compartment for the instances ===
COMPARTMENT_NAME = 'auto-win-compartment'
COMPARTMENT_DESC = 'Compartment for automated Windows instances'
INSTANCE_PREFIX = 'auto-win-instance'
INSTANCE_COUNT = 4
SHAPE = 'VM.Standard3.Flex'
OCPUS = 54
MEMORY_GBS = 512
BOOT_VOL_GBS = 50

print(f"\nCreating compartment '{COMPARTMENT_NAME}'...")
compartment = identity.create_compartment(
    oci.identity.models.CreateCompartmentDetails(
        compartment_id=tenancy_id,
        name=COMPARTMENT_NAME,
        description=COMPARTMENT_DESC
    )
).data
compartment_id = compartment.id
print(f"Compartment OCID: {compartment_id}")

# Wait until the compartment becomes ACTIVE
print("Waiting for compartment to become ACTIVE...")
for _ in range(30):
    comp = identity.get_compartment(compartment_id).data
    if comp.lifecycle_state == 'ACTIVE':
        break
    time.sleep(2)
else:
    raise Exception("Compartment did not become ACTIVE in time.")
print("Compartment is ACTIVE.")

# === 7. Launch the compute instances in the new compartment ===
for idx in range(INSTANCE_COUNT):
    display_name = f"{INSTANCE_PREFIX}-{idx + 1}"
    print(f"Launching {display_name}...")

    # Define the instance launch details
    launch_details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment_id,
        availability_domain=ad_name,
        shape=SHAPE,
        display_name=display_name,
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=win_image_id,
            boot_volume_size_in_gbs=BOOT_VOL_GBS,
        ),
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_GBS,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True
        )
    )

    # Launch the instance
    resp = core.launch_instance(launch_details)
    instance_id = resp.data.id
    print(f"Instance OCID: {instance_id}")

    # Wait for the instance to reach RUNNING state
    print(f"Waiting for {display_name} to become RUNNING...")
    oci.wait_until(
        core,
        core.get_instance(instance_id),
        'lifecycle_state',
        'RUNNING',
        max_wait_seconds=1800
    )
    print(f"{display_name} is RUNNING.")

print("All done! Instances are provisioned in the new compartment.")